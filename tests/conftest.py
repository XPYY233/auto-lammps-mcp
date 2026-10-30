import pytest
from auto_lammps_mcp.service import Service
from auto_lammps_mcp.worker import Worker


@pytest.fixture
def service(tmp_path):
    return Service(tmp_path / "state")


@pytest.fixture
def task(service):
    return service.task_create("Ni study", "Inspect existing scientific output", [{"id": "curve", "description": "energy curve with units"}], "create")


class SchedulerFixture:
    def __init__(self):
        self.submissions = 0
        self.lose_receipt = False
        self.cancelled = []
        self.fail_query = False
        self.state = "running"
        self.staged = []

    def stage(self, run):
        self.staged.append(run["id"])

    def inspect_runtime(self, config, commands, files):
        from auto_lammps_mcp.runtime_probe import fingerprint
        identity = dict(probe_version=1, commands={x: dict(path='/fixture/' + x, sha256='a' * 64, bytes=10) for x in commands}, files={}, libraries={})
        return dict(identity=identity, fingerprint=fingerprint(identity))

    def submit(self, run):
        self.submissions += 1
        if self.lose_receipt:
            raise TimeoutError("injected lost receipt")
        return "12345"

    def reconcile(self, run):
        return "12345" if self.submissions else None

    def query(self, cluster, runs):
        if self.fail_query:
            raise ConnectionError("injected disconnect")
        return {r["job_id"]: dict(state=self.state, exit_code="0:0", core_seconds=320, elapsed_seconds=10, allocated_cores=32) for r in runs}

    def cancel(self, run):
        self.cancelled.append(run["id"])

    def collect(self, run):
        return dict(directory="scheduler-fixture-output")


@pytest.fixture
def scheduler():
    return SchedulerFixture()


@pytest.fixture
def worker(service, scheduler):
    return Worker(service, scheduler)


def revision(service, task):
    return service.task_get(task["id"])["revision"]


def prepare(service, worker, task, intent="first", sample="1"):
    service.setup_configure("cluster", dict(ssh_alias="cluster", remote_root="/project/research", account="research", partition="cpu", environment_script="module load tested-engine"), "cluster")
    root = service.store.root(task["id"])
    (root / "run.sh").write_text("exit 0\n")
    op = service.project_snapshot(task["id"], revision(service, task), "snapshot-" + intent)
    worker.work_one()
    snapshot = service.operation_get(op["operation_id"])["result"]["snapshot_id"]
    service.task_update(task["id"], revision(service, task), {"policy": {"authorized": True, "authorization": "start configured verification"}}, "user authorization", "authorize-" + intent)
    return service.run_prepare(task["id"], revision(service, task), snapshot, "run.sh", intent, "300K", sample, "prepare-" + intent, purpose="environment_check")
