import json

from scripts.build_release_components import main
from scripts.release_manifest_check import artifact_tree_digest


def test_build_components_hashes_model_files_without_machine_path(tmp_path) -> None:
    prompt = tmp_path / "supervisor.txt"
    prompt.write_text("route by domain", encoding="utf-8")
    schema = tmp_path / "tool.json"
    schema.write_text('{"version": 1, "fields": ["id"]}\n', encoding="utf-8")
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text('{"dim": 1024}\n', encoding="utf-8")
    (model / "weights.bin").write_bytes(b"weights")
    cases = tmp_path / "cases.json"
    cases.write_text("[]\n", encoding="utf-8")
    thresholds = tmp_path / "thresholds.json"
    thresholds.write_text('{"answer_non_empty": 1.0}\n', encoding="utf-8")
    digest_cases, _, _ = artifact_tree_digest(cases)
    digest_thresholds, _, _ = artifact_tree_digest(thresholds)
    release = tmp_path / "release.json"
    release.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "eval_release_id": "eval-test-v1",
                "dataset_path": cases.name,
                "dataset_digest": digest_cases,
                "thresholds_path": thresholds.name,
                "thresholds_digest": digest_thresholds,
                "scorer_version": "test",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "components.json"

    assert (
        main(
            [
                "--output",
                str(output),
                "--eval-release",
                str(release),
                "--prompt",
                f"supervisor={prompt}",
                "--model-artifact",
                f"embedding={model}",
                "--tool-schema",
                f"fitness.user.get_current.v1={schema}",
                "--safe-config",
                "agent_max_tool_steps=4",
                "--index-build-id",
                "kb-build-test-1",
            ]
        )
        == 0
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["eval_release_id"] == "eval-test-v1"
    assert payload["index_build_id"] == "kb-build-test-1"
    assert payload["model_artifacts"]["embedding"]["file_count"] == 2
    assert str(tmp_path) not in output.read_text(encoding="utf-8")
