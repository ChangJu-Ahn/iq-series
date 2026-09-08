"""에이전트가 읽는 문서가 실제 시스템과 맞는지 검사한다.

`data-agent-schema.md` 는 Foundry 에이전트에게 주는 지시문이다. 여기 적힌
테이블 이름이 틀리면 에이전트는 없는 테이블을 질의한다. 사람이 읽는 README 와
달리 **아무도 오타를 눈치채지 못한 채** 실습 중에 실패한다.

실제로 `qms_inspection_result` 라고 적혀 있었다. 그런 테이블은 없다. 진짜
이름은 `qms_inspection` 이다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DOC = ROOT / "data-agent-schema.md"

# QMS 레이크하우스가 실제로 만드는 테이블.
#
# ⚠️ 이 목록은 브랜치 `changju-ahn-qms-fabric-lakehouse-design` 의
# `customizing/fabric/qms-lakehouse/src/qms_schema.py` 의 `TABLE_DDL` 키에서
# 떴다. QMS 는 다른 브랜치에 있어 여기서 직접 읽을 수 없으므로 복제해 두었다.
# QMS 가 테이블을 더하거나 이름을 바꾸면 이 목록도 함께 고쳐야 한다.
#
# 복제본이라 QMS 와 이 목록이 **같은 방향으로 함께** 틀릴 수 있다. 그래도
# 오타는 잡는다. 그게 이 테스트의 목적이다.
QMS_TABLES = frozenset(
    {
        "qms_defect_code",
        "qms_disposition",
        "qms_incoming_inspection",
        "qms_inspection",
        "qms_inspection_spec",
        "qms_inspector",
        "qms_measurement",
        "qms_nonconformance",
    }
)


def test_every_qms_table_named_in_the_doc_actually_exists():
    """문서가 부르는 QMS 테이블이 전부 실재해야 한다."""
    text = SCHEMA_DOC.read_text(encoding="utf-8")

    # 컬럼 참조(`qms_inspection.mes_process_result_id`)에서는 점 앞까지만 잡힌다.
    # QMS 컬럼 중 `qms_` 로 시작하는 것은 없으므로 이 패턴은 테이블만 고른다.
    named = set(re.findall(r"qms_[a-z_]+", text))

    unknown = named - QMS_TABLES
    assert not unknown, (
        f"문서가 없는 QMS 테이블을 가리킨다: {sorted(unknown)}\n"
        f"실재하는 것: {sorted(QMS_TABLES)}\n"
        "에이전트는 이 문서를 그대로 믿고 질의한다."
    )
    assert named, "문서에 QMS 테이블 언급이 하나도 없다 — 교차 질의 안내가 사라졌다"


def test_the_doc_points_at_the_direct_mes_to_qms_key():
    """MES→QMS 는 `lot_id` 가 아니라 런 id 로 이어야 한다.

    `lot_id` 로만 이으면 그 로트의 모든 공정 검사가 걸려서 어느 공정의
    검사였는지 흐려진다. 에이전트가 엉뚱한 공정의 판정을 근거로 답하게 된다.
    """
    text = SCHEMA_DOC.read_text(encoding="utf-8")

    assert "mes_process_result_id" in text, (
        "MES→QMS 직접 연결 키가 문서에 없다"
    )
    assert re.search(r"mes_process_result_id.*(null|NULL|None)", text, re.DOTALL), (
        "이 키가 null 일 수 있다는 점(OQC·PCS·EQV)을 알려야 한다. "
        "모르면 에이전트는 조인 결과가 빈 것을 '검사 없음' 으로 오해한다."
    )


def test_the_doc_does_not_promise_columns_fdc_lacks():
    """FDC 에 없는 컬럼을 있는 것처럼 적으면 안 된다.

    이 문서의 핵심은 '답할 수 없는 것' 절이다. 센서는 어떤 로트가 올라와
    있는지 모른다. 그 경계가 흐려지면 에이전트가 추측으로 답한다.
    """
    text = SCHEMA_DOC.read_text(encoding="utf-8")

    marker = "**답할 수 없는**"
    section = text.split(marker)[1] if marker in text else ""
    assert section, "'답할 수 없는 것' 절이 사라졌다"

    for column in ("lot_id", "product_code", "defect_code"):
        assert column in section, (
            f"`{column}` 이 FDC 에 없다는 안내가 사라졌다. "
            "에이전트가 있다고 믿고 추측한다."
        )
