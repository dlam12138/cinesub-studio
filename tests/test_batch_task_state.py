from pathlib import Path

import batch_worker
from batch_worker import BatchConfig, BatchPipeline, TaskState


def test_task_state_default_max_retries_is_three():
    task = TaskState(file="movie.mp4", input_path=str(Path("movie.mp4")))

    assert task.max_retries == 3
    assert task.retry_count == 0


def test_scan_initializes_new_task_with_configured_max_retries(monkeypatch, tmp_path):
    input_dir = tmp_path / "input"
    work_dir = tmp_path / "work"
    output_dir = tmp_path / "output"
    model_dir = tmp_path / "models"
    states_dir = tmp_path / "states"
    archive_dir = tmp_path / "archive"
    failed_dir = tmp_path / "failed"
    input_dir.mkdir()
    media = input_dir / "movie.mp4"
    media.write_bytes(b"fake media")

    monkeypatch.setattr(batch_worker, "DIR_WORK_STATES", states_dir)
    monkeypatch.setattr(batch_worker, "DIR_ARCHIVE", archive_dir)
    monkeypatch.setattr(batch_worker, "DIR_FAILED", failed_dir)

    pipeline = BatchPipeline(
        BatchConfig(
            input_dir=input_dir,
            work_dir=work_dir,
            output_dir=output_dir,
            model_dir=model_dir,
            max_retries=7,
        )
    )

    tasks = pipeline.scan()

    assert len(tasks) == 1
    assert tasks[0].file == "movie.mp4"
    assert tasks[0].max_retries == 7
    assert (states_dir / "movie.state.json").is_file()


def _patch_runtime_dirs(monkeypatch, tmp_path):
    states_dir = tmp_path / "states"
    archive_dir = tmp_path / "archive"
    failed_dir = tmp_path / "failed"
    monkeypatch.setattr(batch_worker, "DIR_WORK_STATES", states_dir)
    monkeypatch.setattr(batch_worker, "DIR_ARCHIVE", archive_dir)
    monkeypatch.setattr(batch_worker, "DIR_FAILED", failed_dir)
    return states_dir


def _pipeline(tmp_path, *, translate=True, translation_mode="bilingual"):
    input_dir = tmp_path / "input"
    input_dir.mkdir(exist_ok=True)
    return BatchPipeline(
        BatchConfig(
            input_dir=input_dir,
            work_dir=tmp_path / "work",
            output_dir=tmp_path / "output",
            model_dir=tmp_path / "models",
            model="small",
            translate=translate,
            target_language="zh-CN",
            translation_mode=translation_mode,
            move_completed=False,
        )
    )


def _save_task(file_name: str, status: str, input_dir: Path) -> TaskState:
    media = input_dir / file_name
    media.write_bytes(b"fake media")
    task = TaskState(
        file=file_name,
        input_path=str(media.resolve()),
        status=status,
        stage=batch_worker.TaskStage.COMPLETED if status == "completed" else status,
        error="boom" if status == "failed" else "",
        error_stage=batch_worker.TaskStage.TRANSLATING if status == "failed" else "",
        retry_count=2 if status == "failed" else 0,
    )
    task.save()
    return task


def _write_required_outputs(pipeline: BatchPipeline, task: TaskState, content: bytes = b"ok") -> None:
    for output in pipeline.required_final_outputs(task):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    task.asr_mode = pipeline.config.asr_mode
    task.language = pipeline.config.language or ""
    task.asr_config_signature = pipeline.config.asr_signature()
    task.save()


def test_prepare_retry_failed_tasks_resets_only_failed(monkeypatch, tmp_path):
    states_dir = _patch_runtime_dirs(monkeypatch, tmp_path)
    pipeline = _pipeline(tmp_path)
    input_dir = pipeline.config.input_dir
    failed = _save_task("failed.mp4", "failed", input_dir)
    completed = _save_task("completed.mp4", "completed", input_dir)
    pending = _save_task("pending.mp4", "pending", input_dir)
    running = _save_task("running.mp4", "running", input_dir)

    plan = batch_worker.prepare_retry_failed_tasks(sorted(states_dir.glob("*.state.json")))

    assert plan.reset_count == 1
    assert plan.untouched_count == 3
    assert plan.selected_task_ids == [failed.file]
    assert TaskState.load(failed.state_path()).status == "pending"
    assert TaskState.load(failed.state_path()).stage == batch_worker.TaskStage.PENDING
    assert TaskState.load(failed.state_path()).error == ""
    assert TaskState.load(failed.state_path()).retry_count == 0
    assert TaskState.load(completed.state_path()).status == "completed"
    assert TaskState.load(pending.state_path()).status == "pending"
    assert TaskState.load(running.state_path()).status == "running"


def test_retry_failed_does_not_scan_or_add_new_input(monkeypatch, tmp_path):
    states_dir = _patch_runtime_dirs(monkeypatch, tmp_path)
    pipeline = _pipeline(tmp_path)
    failed = _save_task("failed.mp4", "failed", pipeline.config.input_dir)
    (pipeline.config.input_dir / "new-file.mp4").write_bytes(b"new media")

    def fail_scan():
        raise AssertionError("retry-failed must not scan input")

    monkeypatch.setattr(pipeline, "scan", fail_scan)
    monkeypatch.setattr(pipeline, "_process_one", lambda task: None)

    assert batch_worker._retry_failed(pipeline) == 0
    assert TaskState.load(failed.state_path()).status == "pending"
    assert not (states_dir / "new-file.state.json").exists()


def test_completed_task_skips_only_when_final_outputs_are_valid(monkeypatch, tmp_path):
    _patch_runtime_dirs(monkeypatch, tmp_path)
    pipeline = _pipeline(tmp_path)
    valid_task = _save_task("valid.mp4", "completed", pipeline.config.input_dir)
    invalid_task = _save_task("invalid.mp4", "completed", pipeline.config.input_dir)
    _write_required_outputs(pipeline, valid_task)
    first_invalid_output = pipeline.required_final_outputs(invalid_task)[0]
    first_invalid_output.parent.mkdir(parents=True, exist_ok=True)
    first_invalid_output.write_bytes(b"")

    tasks = pipeline.scan()

    assert [task.file for task in tasks] == ["invalid.mp4"]
    assert pipeline.completed_outputs_valid(valid_task) is True
    assert pipeline.completed_outputs_valid(invalid_task) is False


def test_completed_final_outputs_follow_translation_mode(monkeypatch, tmp_path):
    _patch_runtime_dirs(monkeypatch, tmp_path)
    pipeline = _pipeline(tmp_path, translation_mode="translated")
    task = _save_task("movie.mp4", "completed", pipeline.config.input_dir)
    _write_required_outputs(pipeline, task)

    required = pipeline.required_final_outputs(task)

    assert any(".translated.zh-CN.srt" in str(path) for path in required)
    assert not any(".bilingual.zh-CN.srt" in str(path) for path in required)
    assert pipeline.completed_outputs_valid(task) is True


def test_completed_output_is_not_reused_when_asr_signature_changes(monkeypatch, tmp_path):
    _patch_runtime_dirs(monkeypatch, tmp_path)
    pipeline = _pipeline(tmp_path)
    task = _save_task("movie.mp4", "completed", pipeline.config.input_dir)
    _write_required_outputs(pipeline, task)
    task.asr_config_signature = "outdated-signature"
    task.save()

    assert pipeline.completed_outputs_valid(task) is False
    assert [row.file for row in pipeline.scan()] == ["movie.mp4"]


def test_stage_reuse_keeps_existing_valid_outputs(monkeypatch, tmp_path):
    _patch_runtime_dirs(monkeypatch, tmp_path)
    pipeline = _pipeline(tmp_path)
    task = _save_task("movie.mp4", "failed", pipeline.config.input_dir)
    audio = pipeline.config.work_dir / "movie.16k.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    audio.write_bytes(b"audio")
    task.audio_path = str(audio)
    _write_required_outputs(pipeline, task, content=b"original")

    monkeypatch.setattr(pipeline, "_extract_audio", lambda input_path: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(pipeline, "_transcribe", lambda audio_path, srt_path: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(
        pipeline,
        "_translate",
        lambda source_srt, output_path, effective_prompt: (_ for _ in ()).throw(AssertionError()),
    )
    monkeypatch.setattr(
        pipeline,
        "_quality_check",
        lambda source_srt, translated_srt, report_path: (_ for _ in ()).throw(AssertionError()),
    )

    before = {path: path.read_bytes() for path in pipeline.required_final_outputs(task)}
    pipeline._process_one(task)
    after = {path: path.read_bytes() for path in pipeline.required_final_outputs(task)}

    assert before == after
    assert audio.read_bytes() == b"audio"
    assert task.status == "completed"
