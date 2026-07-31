import json
import tempfile
import threading
import unittest
from pathlib import Path

from ble_calibration.analysis import (
    DirectionDataset,
    EightDirectionRecomputeService,
)
from ble_calibration.cloud.models import CloudParameters
from ble_calibration.domain import (
    CalibrationProject,
    Direction,
    DirectionRecord,
    DirectionStatus,
)
from ble_calibration.mock.generator import (
    REFERENCE_LOCK_THRESHOLDS,
    REFERENCE_UNLOCK_THRESHOLDS,
    MockConfig,
    generate_mock_session,
)
from ble_calibration.replay import ReplayService
from ble_calibration.session import DirectionSessionController
from ble_calibration.storage import AutosaveWorker, ProjectRepository, StoredProject


class FakeMessage:
    timestamp = 1.25
    arbitration_id = 0x629
    data = b"\x00\x00\x00\x00\xBA"
    channel = 0
    is_fd = True
    bitrate_switch = True


class FakeBlfReader:
    def __init__(self, path):
        self.path = path
        self.stopped = False

    def __iter__(self):
        yield FakeMessage()

    def stop(self):
        self.stopped = True


class StorageAndReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.capture_path = self.root / "session.jsonl"
        frames, manifest = generate_mock_session(MockConfig(seed=20260730))
        self.capture_path.write_text(
            "".join(json.dumps(frame.to_json_record()) + "\n" for frame in frames),
            encoding="utf-8",
        )
        controller = DirectionSessionController()
        datasets = []
        for item in manifest["directions"]:
            direction = Direction.from_label(item["name"])
            controller.select_direction(direction)
            controller.start()
            controller.set_distances(
                item["lock_distance_m"],
                item["unlock_distance_m"],
            )
            for frame in frames:
                if item["start_time"] <= frame.timestamp <= item["end_time"]:
                    controller.process_frame(frame)
            record = controller.manual_stop(item["end_time"])
            datasets.append(
                DirectionDataset(record, controller.samples_for(direction))
            )
        self.datasets = tuple(datasets)
        self.parameters = CloudParameters(
            unlock_thresholds=tuple(REFERENCE_UNLOCK_THRESHOLDS),
            lock_thresholds=tuple(REFERENCE_LOCK_THRESHOLDS),
        )
        self.project = CalibrationProject(
            name="离线八方向",
            original_cloud_hex="00AABB",
            directions=tuple(dataset.record for dataset in self.datasets),
        )
        self.stored = StoredProject(
            project=self.project,
            current_cloud_hex="00AABB",
            capture_path=str(self.capture_path),
            capture_format="jsonl",
            vehicle_name="测试车",
            vehicle_vin="VIN-TEST-001",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_project_round_trip_and_list_survive_reopen(self) -> None:
        database = self.root / "projects.sqlite3"
        with ProjectRepository(database) as repository:
            repository.save_project(self.stored)

        with ProjectRepository(database) as repository:
            loaded = repository.load_project(self.project.project_id)
            summaries = repository.list_projects()

        self.assertEqual(loaded.project.to_dict(), self.project.to_dict())
        self.assertEqual(loaded.capture_path, str(self.capture_path))
        self.assertEqual(loaded.vehicle_vin, "VIN-TEST-001")
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].direction_count, 8)

    def test_parameter_analysis_history_and_recovery(self) -> None:
        result = EightDirectionRecomputeService().recompute(
            self.parameters,
            self.datasets,
            use_actual_action_times=True,
        )
        with ProjectRepository(self.root / "projects.sqlite3") as repository:
            repository.save_project(self.stored)
            history = repository.append_parameter_history(
                self.project.project_id,
                "00CCDD",
                "阈值调整",
            )
            repository.save_analysis(
                self.project.project_id,
                result,
                "00CCDD",
            )
            latest = repository.latest_analysis(self.project.project_id)
            repository.save_recovery(self.stored)
            recovery = repository.load_recovery(self.project.project_id)
            repository.clear_recovery(self.project.project_id)

            self.assertEqual(
                repository.parameter_history(self.project.project_id),
                (history,),
            )
            self.assertEqual(latest.payload["lock_summary"]["total"], 8)
            self.assertEqual(recovery.project.to_dict(), self.project.to_dict())
            self.assertIsNone(repository.load_recovery(self.project.project_id))

    def test_offline_jsonl_rebuild_matches_capture_results(self) -> None:
        with ProjectRepository(self.root / "projects.sqlite3") as repository:
            repository.save_project(self.stored)
            loaded = repository.load_project(self.project.project_id)

        rebuilt = ReplayService().rebuild_project(loaded)
        self.assertEqual(len(rebuilt), 8)
        for original, replayed in zip(self.datasets, rebuilt):
            self.assertEqual(replayed.record, original.record)
            self.assertEqual(replayed.samples, original.samples)

        service = EightDirectionRecomputeService()
        original_result = service.recompute(self.parameters, self.datasets)
        replay_result = service.recompute(self.parameters, rebuilt)
        self.assertEqual(original_result.directions, replay_result.directions)
        self.assertEqual(original_result.lock_summary, replay_result.lock_summary)
        self.assertEqual(original_result.unlock_summary, replay_result.unlock_summary)

    def test_blf_reader_is_lazy_and_frames_are_converted(self) -> None:
        readers = []

        def factory(path):
            reader = FakeBlfReader(path)
            readers.append(reader)
            return reader

        frames = ReplayService(blf_reader_factory=factory).load_frames(
            self.root / "capture.blf",
            "blf",
        )
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].arbitration_id, 0x629)
        self.assertTrue(readers[0].stopped)

    def test_zero_sample_incomplete_direction_replays_as_empty_dataset(self) -> None:
        record = DirectionRecord(
            direction=Direction.FRONT,
            status=DirectionStatus.INCOMPLETE,
            raw_data_file=str(self.root / "empty.jsonl"),
        )
        (self.root / "empty.jsonl").write_text("", encoding="utf-8")
        dataset = ReplayService().rebuild_direction(record, ())
        self.assertEqual(dataset.record, record)
        self.assertEqual(dataset.samples, ())

    def test_autosave_worker_writes_recovery_snapshot(self) -> None:
        saved = threading.Event()

        def snapshot_provider():
            saved.set()
            return self.stored

        with ProjectRepository(self.root / "projects.sqlite3") as repository:
            worker = AutosaveWorker(
                repository,
                snapshot_provider,
                interval_s=0.01,
            )
            worker.start()
            self.assertTrue(saved.wait(1.0))
            worker.stop()
            recovery = repository.load_recovery(self.project.project_id)

        self.assertIsNone(worker.last_error)
        self.assertIsNotNone(recovery)
        self.assertEqual(recovery.project.project_id, self.project.project_id)


if __name__ == "__main__":
    unittest.main()
