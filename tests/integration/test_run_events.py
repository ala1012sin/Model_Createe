"""RunEvent·상태 응답·취소 요청 (D-04). 실제 worker는 P2."""

import pytest

from mlworkbench.core.contracts import DatasetManifest, DatasetState, RunState
from mlworkbench.core.errors import StateTransitionError, SubmitRejected


@pytest.fixture
def run_id(services, approved):
    _, _, jobs = services
    _, _, r = approved
    return jobs.submit(plan_id=r["plan_id"], approval_id=r["approval_id"])["run_id"]


def test_events_monotonic_and_polling(services, run_id):
    _, _, jobs = services
    jobs.append_event(run_id, state=RunState.RUNNING, stage="preprocess", message_code="PREPROCESS_STARTED")
    jobs.append_event(run_id, state=RunState.RUNNING, stage="train", progress=0.6, message_code="TRAIN_PROGRESS")
    st = jobs.get_status(run_id)
    assert [e.seq for e in st.events] == [1, 2, 3] and st.last_seq == 3 and st.stage == "train"
    assert [e.seq for e in jobs.get_status(run_id, after_seq=2).events] == [3]
    assert st.events[1].progress is None  # 진행률 모르면 null


def test_cancel_requires_confirmation_and_is_separate_from_canceled(services, run_id):
    _, _, jobs = services
    with pytest.raises(SubmitRejected):
        jobs.cancel(run_id, "confirm_forged")
    token = jobs.request_cancel_confirmation(run_id)
    assert jobs.cancel(run_id, token)["state"] == "CANCEL_REQUESTED"
    with pytest.raises(SubmitRejected):
        jobs.cancel(run_id, token)  # 1회용
    assert jobs.get_status(run_id).state == RunState.CANCEL_REQUESTED
    jobs.append_event(run_id, state=RunState.CANCELED, stage="train", message_code="WORKER_STOPPED")
    assert jobs.get_status(run_id).state == RunState.CANCELED


def test_invalid_transition_rejected(services, run_id):
    _, _, jobs = services
    jobs.append_event(run_id, state=RunState.RUNNING, stage="train", message_code="X")
    jobs.append_event(run_id, state=RunState.SUCCEEDED, stage="save", message_code="DONE")
    with pytest.raises(StateTransitionError):
        jobs.append_event(run_id, state=RunState.RUNNING, stage="train", message_code="X")


def test_dataset_state_machine(services):
    ds, _, _ = services
    m = ds.register(DatasetManifest(dataset_id="ds_new", name="n.csv", modality="timeseries", source_kind="csv",
                                    state=DatasetState.UPLOADING, upload_progress=0.4))
    ds.transition(m.dataset_id, DatasetState.REGISTERED)
    with pytest.raises(StateTransitionError):
        ds.transition(m.dataset_id, DatasetState.PROFILED)  # PROFILING을 건너뛸 수 없음
    ds.transition(m.dataset_id, DatasetState.PROFILING)
    assert ds.transition(m.dataset_id, DatasetState.PROFILE_FAILED).state == DatasetState.PROFILE_FAILED
