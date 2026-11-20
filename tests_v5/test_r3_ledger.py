from tests_v5.fakebroker import partial, reject
from tests_v5.test_r3_chain import setup
from v5.executor import FAILED, NETTED, allocate


def expected(intent):
    return allocate(
        intent["core_b"], intent["ovl_b"], intent["core_new"], intent["ovl_new"],
        intent["theta"], intent["equity_before"],
    )[2]


def test_full_partial_and_two_attempt_ledgers(tmp_path):
    broker, clock, window, record, executor = setup(tmp_path / "full")
    executor.run_chain(record, window)
    assert executor.load_overlay_fraction() == expected(record.data["intents"][0]) == 0.6

    broker, clock, window, record, executor = setup(tmp_path / "partial")
    broker.script(partial("0.4")); executor.run_chain(record, window)
    for intent in record.data["intents"]:
        assert intent["fraction_after"] == expected(intent)
    assert executor.load_overlay_fraction() == expected(record.data["intents"][-1])


def test_rejection_and_netted_opposing_change(tmp_path):
    broker, clock, window, record, executor = setup(tmp_path / "reject", cash=5000, positions={"TQQQ": 50})
    executor._commit(record, 0.2)
    record.data["target"] = {"core_new": 0.3, "ovl_new": 0.1, "total_new": 0.4}; record.save()
    broker.script(reject(422, 1, "x")); executor.run_chain(record, window)
    assert record.status == FAILED and executor.load_overlay_fraction() == expected(record.data["intents"][0])

    broker, clock, window, record, executor = setup(tmp_path / "net", cash=5000, positions={"TQQQ": 50})
    record.data["target"] = {"core_new": 0, "ovl_new": 0.50005, "total_new": 0.50005}; record.save()
    executor.run_chain(record, window)
    assert record.status == NETTED and executor.load_overlay_fraction() == 1.0 and not broker.orders
