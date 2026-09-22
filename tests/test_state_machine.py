import pytest

from app.models import Request, RequestClass, RequestStatus
from app.security import generate_access_token
from app.state_machine import (
    TransitionError,
    accept_and_queue,
    complete,
    decline,
    move_down,
    move_to_long_actions,
    move_up,
    request_information,
    respond_to_information,
    return_to_queue,
    start_work,
    withdraw,
)


def make_request(request_class=RequestClass.long, status=RequestStatus.submitted, **kwargs) -> Request:
    defaults = dict(
        public_number="REQ-0001",
        access_token=generate_access_token(),
        requester_name="Test Requester",
        requester_email="requester@example.com",
        title="Test title",
        description="Test description",
        request_class=request_class,
        status=status,
    )
    defaults.update(kwargs)
    return Request(**defaults)


def add(db_session, req: Request) -> Request:
    db_session.add(req)
    db_session.flush()
    return req


class TestAcceptAndQueue:
    def test_accepts_submitted_long_action(self, db_session):
        req = add(db_session, make_request())
        accept_and_queue(db_session, req)
        assert req.status == RequestStatus.queued
        assert req.queue_position == 1

    def test_appends_to_bottom_of_existing_queue(self, db_session):
        existing = add(db_session, make_request(status=RequestStatus.queued, queue_position=1, public_number="REQ-0000"))
        req = add(db_session, make_request(public_number="REQ-0002"))
        accept_and_queue(db_session, req)
        assert req.queue_position == 2
        assert existing.queue_position == 1

    def test_rejects_already_queued(self, db_session):
        req = add(db_session, make_request(status=RequestStatus.queued, queue_position=1))
        with pytest.raises(TransitionError):
            accept_and_queue(db_session, req)

    def test_rejects_quick_action(self, db_session):
        req = add(db_session, make_request(request_class=RequestClass.quick, status=RequestStatus.submitted))
        with pytest.raises(TransitionError):
            accept_and_queue(db_session, req)


class TestDecline:
    def test_declines_submitted(self, db_session):
        req = add(db_session, make_request())
        decline(db_session, req, "Not enough context")
        assert req.status == RequestStatus.declined
        assert req.decline_reason == "Not enough context"
        assert req.declined_at is not None

    def test_requires_reason(self, db_session):
        req = add(db_session, make_request())
        with pytest.raises(TransitionError):
            decline(db_session, req, "   ")


class TestStartAndComplete:
    def test_start_work_clears_queue_position_and_renumbers(self, db_session):
        first = add(db_session, make_request(status=RequestStatus.queued, queue_position=1, public_number="REQ-0001"))
        second = add(db_session, make_request(status=RequestStatus.queued, queue_position=2, public_number="REQ-0002"))
        start_work(db_session, first)
        assert first.status == RequestStatus.in_progress
        assert first.queue_position is None
        assert first.previous_queue_position == 1
        assert second.queue_position == 1  # renumbered

    def test_complete_from_in_progress(self, db_session):
        req = add(db_session, make_request(status=RequestStatus.in_progress))
        complete(db_session, req, note="All good")
        assert req.status == RequestStatus.done
        assert req.completed_at is not None
        assert req.completion_note == "All good"

    def test_direct_complete_from_queued(self, db_session):
        req = add(db_session, make_request(status=RequestStatus.queued, queue_position=1))
        complete(db_session, req)
        assert req.status == RequestStatus.done
        assert req.queue_position is None

    def test_return_to_queue_restores_near_previous_position(self, db_session):
        a = add(db_session, make_request(status=RequestStatus.queued, queue_position=1, public_number="REQ-A"))
        b = add(db_session, make_request(status=RequestStatus.queued, queue_position=2, public_number="REQ-B"))
        start_work(db_session, a)
        assert b.queue_position == 1
        return_to_queue(db_session, a)
        assert a.status == RequestStatus.queued
        assert a.queue_position == 1
        assert b.queue_position == 2

    def test_cannot_start_non_queued(self, db_session):
        req = add(db_session, make_request(status=RequestStatus.submitted))
        with pytest.raises(TransitionError):
            start_work(db_session, req)


class TestNeedsInformation:
    def test_request_information_from_queued_removes_from_queue(self, db_session):
        a = add(db_session, make_request(status=RequestStatus.queued, queue_position=1, public_number="REQ-A"))
        b = add(db_session, make_request(status=RequestStatus.queued, queue_position=2, public_number="REQ-B"))
        request_information(db_session, a, "What is the scope?")
        assert a.status == RequestStatus.needs_information
        assert a.previous_status == RequestStatus.queued
        assert a.previous_queue_position == 1
        assert a.queue_position is None
        assert b.queue_position == 1  # renumbered
        assert len(a.info_requests) == 1
        assert a.info_requests[0].owner_question == "What is the scope?"

    def test_respond_restores_queued_state_and_position(self, db_session):
        a = add(db_session, make_request(status=RequestStatus.queued, queue_position=1, public_number="REQ-A"))
        b = add(db_session, make_request(status=RequestStatus.queued, queue_position=2, public_number="REQ-B"))
        request_information(db_session, a, "What is the scope?")
        respond_to_information(db_session, a, "Here is the scope.")
        assert a.status == RequestStatus.queued
        assert a.queue_position == 1
        assert b.queue_position == 2
        assert a.new_information_flag is True
        assert a.info_requests[0].requester_response == "Here is the scope."

    def test_respond_restores_submitted_state(self, db_session):
        req = add(db_session, make_request(status=RequestStatus.submitted))
        request_information(db_session, req, "Question?")
        respond_to_information(db_session, req, "Answer.")
        assert req.status == RequestStatus.submitted

    def test_respond_without_open_request_raises(self, db_session):
        req = add(db_session, make_request(status=RequestStatus.submitted))
        with pytest.raises(TransitionError):
            respond_to_information(db_session, req, "Answer.")

    def test_request_information_requires_question(self, db_session):
        req = add(db_session, make_request(status=RequestStatus.submitted))
        with pytest.raises(TransitionError):
            request_information(db_session, req, "")


class TestWithdraw:
    def test_withdraw_from_queued_removes_from_queue(self, db_session):
        a = add(db_session, make_request(status=RequestStatus.queued, queue_position=1, public_number="REQ-A"))
        b = add(db_session, make_request(status=RequestStatus.queued, queue_position=2, public_number="REQ-B"))
        withdraw(db_session, a)
        assert a.status == RequestStatus.withdrawn
        assert a.queue_position is None
        assert b.queue_position == 1

    def test_cannot_withdraw_done(self, db_session):
        req = add(db_session, make_request(status=RequestStatus.done))
        with pytest.raises(TransitionError):
            withdraw(db_session, req)


class TestMoveToLongActions:
    def test_moves_quick_to_bottom_of_long_queue(self, db_session):
        existing_long = add(
            db_session,
            make_request(status=RequestStatus.queued, queue_position=1, public_number="REQ-LONG"),
        )
        quick = add(
            db_session,
            make_request(request_class=RequestClass.quick, status=RequestStatus.queued, public_number="REQ-QUICK"),
        )
        move_to_long_actions(db_session, quick)
        assert quick.request_class == RequestClass.long
        assert quick.status == RequestStatus.queued
        assert quick.queue_position == 2
        assert existing_long.queue_position == 1

    def test_rejects_long_action(self, db_session):
        req = add(db_session, make_request(status=RequestStatus.queued, queue_position=1))
        with pytest.raises(TransitionError):
            move_to_long_actions(db_session, req)


class TestReordering:
    def _queue_of(self, db_session, n):
        reqs = []
        for i in range(1, n + 1):
            reqs.append(
                add(db_session, make_request(status=RequestStatus.queued, queue_position=i, public_number=f"REQ-{i}"))
            )
        return reqs

    def test_move_up(self, db_session):
        a, b, c = self._queue_of(db_session, 3)
        move_up(db_session, b)
        assert [x.queue_position for x in (a, b, c)] == [2, 1, 3]

    def test_move_up_at_top_is_noop(self, db_session):
        a, b, c = self._queue_of(db_session, 3)
        move_up(db_session, a)
        assert [x.queue_position for x in (a, b, c)] == [1, 2, 3]

    def test_move_down(self, db_session):
        a, b, c = self._queue_of(db_session, 3)
        move_down(db_session, a)
        assert [x.queue_position for x in (a, b, c)] == [2, 1, 3]

    def test_move_down_at_bottom_is_noop(self, db_session):
        a, b, c = self._queue_of(db_session, 3)
        move_down(db_session, c)
        assert [x.queue_position for x in (a, b, c)] == [1, 2, 3]
