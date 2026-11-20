"""The development runner must refuse unproven isolation before any DB write."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from app.agent.routing import RuleIntentRouter
from app.evaluation.task_eval import dev_real_rag as rag

SERVICE = Path(__file__).resolve().parents[2]
SCRIPT = SERVICE / "scripts/task_eval_rag_real.sh"


@pytest.mark.parametrize("case_id", [f"KNOW-{number:03d}" for number in range(1, 8)])
def test_fixed_development_profiles_match_original_case_and_bound_synthetic_corpus(case_id):
    source = SERVICE / "evals/task_eval/datasets/v1.jsonl"
    cases = [json.loads(line) for line in source.read_text().splitlines()]
    case = next(item for item in cases if item["case_id"] == case_id)
    profile = rag._profile(case_id)
    assert case["split"] == "dev" and case["review"]["status"] == "draft"
    assert profile.query == case["steps"][0]["message"]
    assert len(profile.documents) == 3
    assert profile.documents[0][0:2] == (profile.relevant_id, "eval-org-a")
    assert profile.documents[1][0:2] == ("eval-decoy", "eval-org-a")
    assert profile.documents[2][0:2] == ("eval-foreign", "eval-org-b")
    assert len(rag._fixture_hash(profile)) == 64
    assert rag._fixture_hash(profile) == rag._fixture_hash(rag._profile(case_id))


def test_development_profiles_reject_unbound_test_and_injected_cases():
    for case_id in ("KNOW-008", "KNOW-020", "SAFE-013", "", "KNOW-001\nother"):
        with pytest.raises(ValueError, match="unsupported synthetic"):
            rag._profile(case_id)


def test_unanswerable_development_fixture_is_distinct_and_frozen():
    source = SERVICE / "evals/task_eval/datasets/v1.jsonl"
    cases = [json.loads(line) for line in source.read_text().splitlines()]
    case = next(item for item in cases if item["case_id"] == "KNOW-011")
    profile = rag._profile("KNOW-011")
    assert case["split"] == "dev" and case["review"]["status"] == "draft"
    assert profile.query == case["steps"][0]["message"]
    assert profile.relevant_id is None
    assert profile.documents == (
        ("eval-decoy", "eval-org-a", "合成示例：合理安排休息和训练时间。"),
        ("eval-foreign", "eval-org-b", "其他机构专用的合成运动记录。"),
    )
    assert len(rag._fixture_hash(profile)) == 64


@pytest.mark.asyncio
@pytest.mark.parametrize("case_id", [*[f"KNOW-{number:03d}" for number in range(1, 8)], "KNOW-011"])
async def test_profile_query_reaches_knowledge_route_without_hidden_router_model(case_id):
    decision = await RuleIntentRouter().decide(
        rag._profile(case_id).query, recent_messages=[], previous_route=None
    )
    assert len(decision.intents) == 1
    assert decision.intents[0].route == "FITNESS_COACHING"
    assert decision.intents[0].intent == "FITNESS_KNOWLEDGE_QA"


@pytest.mark.asyncio
async def test_seed_writes_exact_case_bound_documents_and_embeddings():
    profile = rag._profile("KNOW-007")

    class Models:
        async def embed(self, texts):
            assert texts == [item[2] for item in profile.documents]
            return [[float(index)] for index in range(len(texts))]

    class Repository:
        def __init__(self):
            self.calls = []

        async def replace_document(self, document, chunks, embeddings):
            self.calls.append((document, chunks, embeddings))

    repository = Repository()
    await rag.seed(repository, Models(), profile)
    assert len(repository.calls) == 3
    for (document, chunks, embeddings), (identifier, organization, text) in zip(
        repository.calls, profile.documents, strict=True
    ):
        assert document.id == identifier and document.organization_id == organization
        assert len(chunks) == 1 and chunks[0].id == identifier and chunks[0].content == text
        assert len(embeddings) == 1


def environment() -> dict[str, str]:
    return {
        "TASK_EVAL_RAG_RUN_ID": "a" * 16,
        "TASK_EVAL_RAG_PG_ID": "b" * 64,
        "TASK_EVAL_RAG_NETWORK_ID": "c" * 64,
        "TASK_EVAL_RAG_PG_PORT": "35432",
        "POSTGRES_PASSWORD": "synthetic-password",
        "AGENT_DATABASE_URL": "postgresql+asyncpg://fitness_eval:synthetic-password@127.0.0.1:35432/postgres",
    }


def test_isolation_checks_container_network_ephemeral_volume_and_exact_port(monkeypatch):
    values = iter(
        [
            f"{'a' * 16}|pgvector/pgvector:pg16|{'c' * 64}|true",
            '{"/var/lib/postgresql/data":""}',
            f"false|{'a' * 16}",
            f'{{"{"b" * 64}":{{}}}}',
            "127.0.0.1:35432",
        ]
    )
    monkeypatch.setattr(rag, "_docker_output", lambda *args: next(values))
    rag.validate_isolated_environment(environment())


@pytest.mark.parametrize(
    "name,value",
    [
        (
            "AGENT_DATABASE_URL",
            "postgresql+asyncpg://fitness_eval:p@production.example:35432/postgres",
        ),
        ("TASK_EVAL_RAG_PG_PORT", "5432;bad"),
        ("TASK_EVAL_RAG_PG_ID", "invalid"),
    ],
)
def test_invalid_database_or_container_never_reaches_docker(monkeypatch, name, value):
    env = environment()
    env[name] = value
    monkeypatch.setattr(
        rag, "_docker_output", lambda *args: pytest.fail("unexpected docker inspection")
    )
    with pytest.raises(ValueError):
        rag.validate_isolated_environment(env)


def test_remote_docker_host_is_rejected_before_any_command(tmp_path):
    fake = tmp_path / "docker"
    marker = tmp_path / "called"
    fake.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 0\n")
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "DOCKER_HOST": "tcp://remote:2375"}
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, timeout=5, check=False
    )
    assert result.returncode != 0
    assert not marker.exists()


def test_remote_docker_context_is_rejected_before_network_create(tmp_path):
    fake = tmp_path / "docker"
    marker = tmp_path / "calls"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$*\" >> '{marker}'\n"
        "if [ \"$1 $2\" = 'context show' ]; then echo remote; "
        "elif [ \"$1 $2\" = 'context inspect' ]; then echo tcp://remote:2375; "
        "else exit 9; fi\n"
    )
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "DOCKER_HOST": ""}
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, timeout=5, check=False
    )
    assert result.returncode != 0
    calls = marker.read_text()
    assert "context show" in calls and "context inspect" in calls
    assert "network create" not in calls and " run " not in calls


def test_cleanup_attempts_network_even_when_container_removal_fails(tmp_path):
    fake = tmp_path / "docker"
    marker = tmp_path / "calls"
    fake.write_text(
        "#!/bin/sh\n"
        f"echo \"$1 $2\" >> '{marker}'\n"
        'case "$1 $2" in\n'
        "  'context show') echo local;;\n"
        "  'context inspect') echo unix:///tmp/docker.sock;;\n"
        "  'network create') echo network-id;;\n"
        "  'run -d') echo container-id;;\n"
        "  'exec container-id') exit 0;;\n"
        "  'port container-id') exit 4;;\n"
        "  'rm -f') exit 5;;\n"
        "  'network rm') exit 0;;\n"
        "  *) exit 9;;\n"
        "esac\n"
    )
    fake.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:/usr/bin:/bin", "DOCKER_HOST": ""}
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=env, capture_output=True, timeout=5, check=False
    )
    assert result.returncode != 0
    calls = marker.read_text()
    assert "rm -f" in calls and "network rm" in calls
