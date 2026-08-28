"""检查 Alembic 迁移链是否满足发布前的基础兼容性契约。"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MigrationRevision:
    """只保存迁移模块的声明信息，不执行迁移文件中的 upgrade 代码。"""

    file_path: Path
    revision: str
    down_revisions: tuple[str, ...]
    has_upgrade: bool
    has_downgrade: bool


@dataclass(frozen=True)
class MigrationContractResult:
    """迁移契约检查结果，供命令行和单元测试共同使用。"""

    revisions: tuple[MigrationRevision, ...]
    heads: tuple[str, ...]
    errors: tuple[str, ...]

    @property
    def passed(self) -> bool:
        """只有没有错误时才允许进入后续发布门禁。"""

        return not self.errors


def _literal_assignment(tree: ast.Module, name: str) -> object:
    """读取模块顶层的常量赋值，避免 import 迁移文件触发数据库操作。"""

    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        if isinstance(target, ast.Name) and target.id == name and value is not None:
            return ast.literal_eval(value)
    raise ValueError(f"缺少顶层声明：{name}")


def _as_revision_tuple(value: object, field_name: str) -> tuple[str, ...]:
    """将 Alembic 支持的字符串或序列父版本统一为不可变字符串元组。"""

    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ValueError(f"{field_name} 必须是字符串、字符串序列或 None")


def _read_revision(path: Path) -> MigrationRevision:
    """解析单个迁移文件的版本声明和双向迁移函数。"""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    revision = _literal_assignment(tree, "revision")
    if not isinstance(revision, str) or not revision:
        raise ValueError("revision 必须是非空字符串")
    down_revisions = _as_revision_tuple(_literal_assignment(tree, "down_revision"), "down_revision")
    functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    return MigrationRevision(
        file_path=path,
        revision=revision,
        down_revisions=down_revisions,
        has_upgrade="upgrade" in functions,
        has_downgrade="downgrade" in functions,
    )


def validate_migrations(migrations_dir: Path) -> MigrationContractResult:
    """验证迁移链、父版本引用和 upgrade/downgrade 双向入口。"""

    errors: list[str] = []
    revisions: list[MigrationRevision] = []
    files = sorted(path for path in migrations_dir.glob("*.py") if path.name != "__init__.py")
    if not files:
        errors.append(f"迁移目录没有发现 Python 文件：{migrations_dir}")

    for path in files:
        try:
            revisions.append(_read_revision(path))
        except (SyntaxError, ValueError, OSError) as exc:
            errors.append(f"{path.name}: {exc}")

    by_revision: dict[str, MigrationRevision] = {}
    for item in revisions:
        if item.revision in by_revision:
            errors.append(
                f"重复 revision={item.revision}：{by_revision[item.revision].file_path.name} 和 {item.file_path.name}"
            )
        else:
            by_revision[item.revision] = item
        if not item.has_upgrade:
            errors.append(f"{item.file_path.name}: 缺少 upgrade()")
        if not item.has_downgrade:
            errors.append(f"{item.file_path.name}: 缺少 downgrade()")

    referenced: set[str] = set()
    for item in revisions:
        for parent in item.down_revisions:
            referenced.add(parent)
            if parent not in by_revision:
                errors.append(f"{item.file_path.name}: down_revision={parent} 不存在")

    heads = tuple(sorted(set(by_revision) - referenced))
    if len(heads) != 1:
        errors.append(f"迁移链必须只有一个 head，当前为 {list(heads)}")
    return MigrationContractResult(tuple(revisions), heads, tuple(errors))


def main() -> int:
    """执行发布前检查并输出脱敏的迁移链摘要。"""

    migrations_dir = Path(__file__).resolve().parents[1] / "migrations" / "versions"
    result = validate_migrations(migrations_dir)
    if result.passed:
        root_revisions = sorted(
            item.revision for item in result.revisions if not item.down_revisions
        )
        print(
            f"[通过] migration-chain: revisions={len(result.revisions)} "
            f"root={root_revisions} head={result.heads}"
        )
        print("[通过] migration-contract: 每个版本均包含 upgrade()/downgrade()，父版本引用完整")
        return 0

    for error in result.errors:
        print(f"[失败] migration-contract: {error}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
