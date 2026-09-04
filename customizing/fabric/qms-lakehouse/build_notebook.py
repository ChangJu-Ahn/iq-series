"""src/ 모듈을 인라인 전개해 Fabric 노트북을 조립한다.

Fabric 노트북은 파일 업로드나 pip install 없이 단독 실행되어야 한다. 그래서
모듈을 그대로 셀에 붙이고 로컬 import 행만 지운다. 지운 뒤에도 모든 이름이
한 네임스페이스에 모이므로 참조는 그대로 성립한다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import nbformat

MODULE_ORDER = (
    "qms_reference",
    "mes_client",
    "qms_masters",
    "qms_inspection",
    "qms_measurement",
    "qms_incoming",
    "qms_nonconformance",
    "qms_validate",
    "qms_schema",
)

# 괄호 묶음 여러 줄 import 도 통째로 잡는다. import 목록에 중첩 괄호가 없어
# [^)]* 로 충분하다.
_LOCAL_IMPORT = re.compile(
    r"^from\s+src\.[a-z_]+\s+import\s+(?:\([^)]*\)|[^\n]*)\n",
    re.MULTILINE,
)

_INTRO = """# QMS 가상 데이터 시드 노트북

Mock MES를 실시간 조회해 그와 맞물리는 QMS 데이터 1,004행(8테이블)을 만들고
이 레이크하우스에 Delta 테이블로 적재합니다.

## 연동 원칙

MES와 QMS는 서로 다른 시스템입니다. DB 수준의 외래키는 없고
`lot_id`, `product_code`, `step_code`, `material_code` 라는 비즈니스 키로만 이어집니다.
같은 사실을 양쪽이 중복해서 갖지 않습니다. QMS에는 `mes_result`, `scrap_qty`,
`operator`, `in_qty`, `out_qty` 컬럼이 없습니다. 생산 결과를 알고 싶으면 MES에 물어야 합니다.

## 실행 순서

파라미터 셀에서 레이크하우스 이름과 MES API 키 조달 방법을 정한 뒤 전체 실행하세요.
고정 시드와 `overwrite` 모드를 쓰므로 몇 번을 다시 돌려도 결과가 같습니다.
"""

_PARAMETERS = '''# Fabric 파이프라인에서 이 셀의 값을 덮어쓸 수 있습니다.
LAKEHOUSE_NAME = "QMS_LH"
MES_BASE_URL = "https://mock-mes.greenrock-bb44c93a.koreacentral.azurecontainerapps.io"

# 키를 여기에 적지 마세요. 비워 두면 Key Vault, 그다음 환경변수 순으로 찾습니다.
MES_API_KEY = ""
KEY_VAULT_URL = ""
KEY_VAULT_SECRET_NAME = "mes-api-key"

TABLE_PREFIX = "qms_"
WRITE_MODE = "overwrite"
'''

_RESOLVE_KEY = '''import os


def resolve_api_key() -> str:
    """파라미터 → Key Vault → 환경변수 순으로 MES API 키를 찾는다."""
    if MES_API_KEY:
        return MES_API_KEY
    if KEY_VAULT_URL:
        try:
            import notebookutils

            secret = notebookutils.credentials.getSecret(KEY_VAULT_URL, KEY_VAULT_SECRET_NAME)
            if secret:
                return secret
        except Exception as exc:
            print(f"Key Vault 조회를 건너뜁니다: {exc}")
    return os.environ.get("MES_API_KEY", "")


RESOLVED_API_KEY = resolve_api_key()
print("API 키 확보됨" if RESOLVED_API_KEY else "API 키 없음. 파라미터 셀, Key Vault, 환경변수 중 하나를 설정하세요.")
'''

_GATE = '''# MES 연결 게이트. REST 와 MCP 두 채널이 모두 살아 있어야 진행합니다.
CLIENT = MesClient(MES_BASE_URL, RESOLVED_API_KEY)
try:
    SNAPSHOT = CLIENT.fetch_snapshot()
except Exception as exc:
    raise RuntimeError(
        "MES 연결에 실패했습니다. 확인할 것: "
        "(1) API 키가 올바른가 (2) Fabric Spark 풀에서 외부 인터넷 아웃바운드가 허용되는가 "
        f"(3) {MES_BASE_URL} 가 응답하는가. 원인: {exc}"
    ) from exc

print(
    f"제품 {len(SNAPSHOT.products)} · 자재 {len(SNAPSHOT.materials)} · BOM {len(SNAPSHOT.bom)} · "
    f"로트 {len(SNAPSHOT.lots)} · 공정이력 {len(SNAPSHOT.process_results)} · "
    f"라우트 {len(SNAPSHOT.route)} · 설비 {len(SNAPSHOT.equipment)}"
)
'''

_BUILD = '''TABLES = build_all_tables(SNAPSHOT)
for name, rows in TABLES.items():
    print(f"{name:26} {len(rows):5}행")
print(f"{'합계':24} {sum(len(rows) for rows in TABLES.values()):5}행")
'''

_VALIDATE = '''# 적재 전에 검증합니다. 이미 쓴 Delta 테이블은 되돌릴 수 없기 때문입니다.
RESULTS = validate(SNAPSHOT, TABLES)
print(format_report(RESULTS))
raise_on_fatal(RESULTS)
'''

_LOAD = '''for name, rows in TABLES.items():
    assert name.startswith(TABLE_PREFIX), f"테이블 접두사 규칙 위반: {name}"
    target = f"{LAKEHOUSE_NAME}.{name}" if LAKEHOUSE_NAME else name
    frame = spark.createDataFrame(to_rows(name, rows), schema=TABLE_DDL[name])
    frame.write.format("delta").mode(WRITE_MODE).saveAsTable(target)
    print(f"{target:34} {frame.count():5}행 적재")
'''

_OUTRO = """## 다음 단계

1. 레이크하우스에서 `qms_` 8개 테이블을 확인합니다.
2. 이 레이크하우스를 원본으로 Fabric Data Agent를 만들고, `data-agent-schema.md`의
   설명을 에이전트 지식으로 넣습니다.
3. Foundry 에이전트에 이 Data Agent와 MES MCP 엔드포인트를 함께 붙입니다.

## 두 시스템을 함께 봐야 답이 나오는 질문

- 생산은 통과했는데 품질이 잡아 세운 로트는 무엇이고 왜 그런가요? (3건)
- 생산에서 불합격인데 출하가 승인된 로트가 있나요? 승인 근거는요? (2건)
- 설비는 불량으로 판정하지 않았는데 검사에서 결함이 나온 건은요? (4건)
- 입고검사에서 불합격난 자재가 실제로 투입된 로트를 추적해 주세요. (2건)
- 재작업을 했지만 결국 폐기된 로트는요? (2건)
"""


def strip_local_imports(source: str) -> str:
    """src. 로 시작하는 import 행만 제거한다. 표준 라이브러리 import 는 남긴다."""
    return _LOCAL_IMPORT.sub("", source)


def _code(source: str, **metadata) -> nbformat.NotebookNode:
    cell = nbformat.v4.new_code_cell(source.rstrip() + "\n")
    cell.metadata.update(metadata)
    return cell


def build_notebook(root: Path) -> nbformat.NotebookNode:
    notebook = nbformat.v4.new_notebook()
    notebook.metadata.update(
        {
            "kernelspec": {
                "display_name": "Synapse PySpark",
                "language": "Python",
                "name": "synapse_pyspark",
            },
            "language_info": {"name": "python"},
        }
    )
    cells = [nbformat.v4.new_markdown_cell(_INTRO)]
    cells.append(_code(_PARAMETERS, tags=["parameters"]))
    cells.append(_code(_RESOLVE_KEY))
    for module in MODULE_ORDER:
        source = (root / "src" / f"{module}.py").read_text(encoding="utf-8")
        cells.append(_code(strip_local_imports(source), qms_cell="module", qms_module=module))
    cells.append(_code(_GATE))
    cells.append(_code(_BUILD))
    cells.append(_code(_VALIDATE))
    cells.append(_code(_LOAD))
    cells.append(nbformat.v4.new_markdown_cell(_OUTRO))
    notebook.cells = cells
    return notebook


def main() -> None:
    root = Path(__file__).resolve().parent
    target = root / "qms_lakehouse_seed.ipynb"
    notebook = build_notebook(root)
    nbformat.validate(notebook)
    nbformat.write(notebook, target)
    print(f"{target} 생성됨 · {len(notebook.cells)}셀")


if __name__ == "__main__":
    sys.exit(main())
