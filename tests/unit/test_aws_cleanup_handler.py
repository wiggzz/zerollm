from __future__ import annotations

import sys

from control_plane.backends.aws import handlers


def test_terminate_instances_for_stack_terminates_active_tagged_instances(monkeypatch):
    class FakeEC2:
        def __init__(self):
            self.filters = None
            self.terminated = None

        def describe_instances(self, Filters):
            self.filters = Filters
            return {
                "Reservations": [
                    {
                        "Instances": [
                            {"InstanceId": "i-running"},
                            {"InstanceId": "i-stopped"},
                        ]
                    }
                ]
            }

        def terminate_instances(self, InstanceIds):
            self.terminated = InstanceIds

    fake_ec2 = FakeEC2()

    class FakeBoto3:
        @staticmethod
        def client(name):
            assert name == "ec2"
            return fake_ec2

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3)

    terminated = handlers._terminate_instances_for_stack("stack-123")

    assert terminated == ["i-running", "i-stopped"]
    assert fake_ec2.terminated == ["i-running", "i-stopped"]
    assert {"Name": "tag:zerollm:stack-id", "Values": ["stack-123"]} in fake_ec2.filters
    assert {
        "Name": "instance-state-name",
        "Values": ["pending", "running", "stopping", "stopped"],
    } in fake_ec2.filters


def test_terminate_instances_for_stack_noops_when_no_instances(monkeypatch):
    class FakeEC2:
        def describe_instances(self, Filters):
            return {"Reservations": []}

        def terminate_instances(self, InstanceIds):
            raise AssertionError("terminate_instances should not be called")

    class FakeBoto3:
        @staticmethod
        def client(name):
            assert name == "ec2"
            return FakeEC2()

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3)

    assert handlers._terminate_instances_for_stack("stack-123") == []
