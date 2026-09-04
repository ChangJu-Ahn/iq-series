"""QMS 도메인 참조 상수.

MES에서 오지 않는 값은 전부 여기에 있다. 생성 모듈은 이 상수와 MesSnapshot만
읽고 동작하므로, 값을 바꾸면 결과가 어떻게 달라지는지 한곳에서 파악된다.
"""

from __future__ import annotations

import datetime as dt

BASE_DATE = dt.date(2026, 9, 4)

# 모듈마다 독립 시드를 둔다. 한 모듈의 난수 소비량이 바뀌어도
# 다른 모듈의 출력이 흔들리지 않게 하기 위함이다.
SEED_MASTERS = 20260904
SEED_INSPECTION = 20260905
SEED_MEASUREMENT = 20260906
SEED_INCOMING = 20260907
SEED_NCR = 20260908

MES_DEFECT_CODES = (
    "Particle",
    "Scratch",
    "Overlay",
    "Etch-Residue",
    "Contamination",
    "CD-OOS",
)

SEVERITY_SCORE = {"Critical": 9, "Major": 6, "Minor": 3}

# (mes_defect_code, 코드 접두, defect_category, 주 발생 공정 CSV)
DEFECT_TAXONOMY = (
    ("Particle", "PTC", "오염", "DIFF,CVD,CMP"),
    ("Scratch", "SCR", "외관", "CMP,PKG"),
    ("Overlay", "OVL", "패턴", "PHOTO,METRO"),
    ("Etch-Residue", "ETR", "막질", "ETCH,CVD"),
    ("Contamination", "CTM", "오염", "DIFF,IMPL,CVD"),
    ("CD-OOS", "CDO", "치수", "PHOTO,ETCH,METRO"),
)

# (mes_defect_code, 순번, 한글명, 영문명, severity, 표준원인, 표준조치)
DEFECT_DETAILS = (
    ("Particle", 1, "파티클 오염 0.12um 이상", "Particle contamination over 0.12um", "Critical", "챔버 내벽 박리물 낙하", "챔버 습식세정 및 시즈닝 재실시"),
    ("Particle", 2, "장비 유래 파티클", "Equipment-borne particle", "Major", "이송 로봇 마모 분진", "로봇 암 교체 및 파티클 카운트 재측정"),
    ("Particle", 3, "가스라인 유래 파티클", "Gas line particle", "Major", "가스 필터 수명 초과", "인라인 필터 교체 및 퍼지"),
    ("Particle", 4, "인체 유래 파티클", "Human-borne particle", "Minor", "방진복 착용 절차 미준수", "클린룸 입실 교육 및 에어샤워 점검"),
    ("Scratch", 1, "CMP 연마 스크래치", "CMP polish scratch", "Critical", "슬러리 내 응집 입자", "슬러리 필터 교체 및 유량 재설정"),
    ("Scratch", 2, "웨이퍼 핸들링 스크래치", "Wafer handling scratch", "Major", "척 표면 이물", "척 세정 및 진공압 점검"),
    ("Scratch", 3, "캐리어 접촉 흠집", "Carrier contact mark", "Minor", "FOUP 슬롯 변형", "FOUP 교체 및 정렬 보정"),
    ("Scratch", 4, "패키지 표면 손상", "Package surface damage", "Minor", "몰드 이형 불량", "이형제 도포량 조정"),
    ("Overlay", 1, "정렬도 X 방향 초과", "Overlay X out of tolerance", "Critical", "스테이지 열변형", "스캐너 열보정 및 재정렬"),
    ("Overlay", 2, "정렬도 Y 방향 초과", "Overlay Y out of tolerance", "Critical", "레티클 장착 편차", "레티클 재장착 및 얼라인 재수행"),
    ("Overlay", 3, "회전 성분 편차", "Rotation component deviation", "Major", "웨이퍼 노치 정렬 오차", "노치 얼라이너 캘리브레이션"),
    ("Overlay", 4, "배율 성분 편차", "Magnification deviation", "Minor", "렌즈 온도 드리프트", "렌즈 온도 제어 루프 재조정"),
    ("Etch-Residue", 1, "폴리머 잔류물", "Polymer residue", "Critical", "에천트 조성 이탈", "가스 유량비 재설정 및 챔버 컨디셔닝"),
    ("Etch-Residue", 2, "금속 잔류물", "Metal residue", "Major", "오버에치 시간 부족", "에치 타임 연장 및 EPD 신호 재검토"),
    ("Etch-Residue", 3, "하드마스크 잔류", "Hard mask residue", "Major", "스트립 공정 누락", "애싱 레시피 보완"),
    ("Etch-Residue", 4, "측벽 잔류물", "Sidewall residue", "Minor", "패시베이션 과다", "패시베이션 가스 비율 하향"),
    ("Contamination", 1, "금속 오염 Cu", "Metallic contamination Cu", "Critical", "금속 배선 공정 교차 오염", "전용 챔버 분리 및 웨이퍼 세정"),
    ("Contamination", 2, "유기물 오염", "Organic contamination", "Major", "포토레지스트 잔류", "UV 오존 세정 추가"),
    ("Contamination", 3, "수분 오염", "Moisture contamination", "Major", "로드락 퍼지 부족", "퍼지 시간 연장 및 진공도 확인"),
    ("Contamination", 4, "이온성 오염", "Ionic contamination", "Minor", "초순수 비저항 저하", "UPW 라인 재생 및 수질 재측정"),
    ("CD-OOS", 1, "선폭 상한 초과", "CD above upper limit", "Critical", "노광량 부족", "도즈 재설정 및 FEM 재평가"),
    ("CD-OOS", 2, "선폭 하한 미달", "CD below lower limit", "Critical", "현상 시간 과다", "현상 레시피 시간 단축"),
    ("CD-OOS", 3, "선폭 균일도 이탈", "CD uniformity out of spec", "Major", "핫플레이트 온도 편차", "베이크 플레이트 존별 온도 보정"),
    ("CD-OOS", 4, "라인 에지 러프니스", "Line edge roughness", "Minor", "레지스트 감도 편차", "레지스트 로트 교체 및 재평가"),
)

# characteristic_code -> (한글명, measurement_type, unit, target, lsl, usl)
CHARACTERISTIC_BASE = {
    "CD": ("선폭", "계량형", "nm", 45.0, 40.5, 49.5),
    "OVL": ("정렬도", "계량형", "nm", 2.0, 0.0, 4.0),
    "THK": ("막두께", "계량형", "um", 1.20, 1.10, 1.30),
    "PTC": ("파티클수", "계수형", "ea", 8.0, 0.0, 20.0),
    "RS": ("면저항", "계량형", "Ω·sq", 120.0, 108.0, 132.0),
    "WRP": ("휨", "계량형", "%", 0.35, 0.05, 0.80),
}

# 공정별 관리 특성 3종. 4제품 × 9공정 × 3특성 = 108 검사기준.
STEP_CHARACTERISTICS = {
    "DIFF": ("THK", "PTC", "RS"),
    "PHOTO": ("CD", "OVL", "PTC"),
    "ETCH": ("CD", "THK", "PTC"),
    "IMPL": ("RS", "PTC", "THK"),
    "CVD": ("THK", "PTC", "RS"),
    "CMP": ("THK", "WRP", "PTC"),
    "METRO": ("CD", "OVL", "THK"),
    "TEST": ("RS", "CD", "PTC"),
    "PKG": ("WRP", "PTC", "THK"),
}

# 미세 노드일수록 선폭 규격이 좁다. CD 특성에만 적용한다.
PRODUCT_CD_SCALE = {"LX9": 0.55, "DDR5": 1.00, "NAND": 1.60, "PMIC": 2.40}

# QMS 계측기. MES 생산설비(EQP-*)와 완전히 별개 자산이다.
METROLOGY_EQP = {
    "CD": "MET-CD01",
    "OVL": "MET-OVL01",
    "THK": "MET-THK01",
    "PTC": "MET-PTC01",
    "RS": "MET-RS01",
    "WRP": "MET-WRP01",
}

SAMPLING_METHODS = {
    "계량형": ("5매 랜덤 9포인트", 5),
    "계수형": ("3매 전면 스캔", 3),
}

INSPECTION_FREQUENCIES = {
    "CD": "로트별",
    "OVL": "로트별",
    "THK": "로트별",
    "PTC": "전수",
    "RS": "시간별",
    "WRP": "시간별",
}

CONTROL_METHODS = {
    "계량형": "X-bar R 관리도",
    "계수형": "u 관리도",
}

# (팀명, 자격 보유 특성 CSV)
INSPECTOR_TEAMS = (
    ("계측팀", "CD,OVL,THK"),
    ("입고검사팀", "PTC,THK"),
    ("신뢰성팀", "RS,WRP"),
    ("출하검사팀", "CD,RS,PTC"),
    ("품질보증팀", "CD,OVL,THK,PTC,RS,WRP"),
)

# MES operator(kim.js 등)와 겹치지 않는 별도 인력 15명.
INSPECTOR_NAMES = (
    "강민우", "노현서", "서지훈", "오세영", "유다은",
    "임채원", "한도윤", "홍서아", "문가온", "배준호",
    "신예린", "안태경", "윤소민", "조하람", "하시우",
)

QUALIFICATION_LEVELS = ("초급", "중급", "선임", "책임")

# (supplier_code, supplier_name_ko)
SUPPLIERS = (
    ("SUP-A01", "한빛머티리얼즈"),
    ("SUP-A02", "동방정밀소재"),
    ("SUP-B01", "세종케미칼"),
    ("SUP-B02", "대륙화학"),
    ("SUP-C01", "성진가스"),
    ("SUP-C02", "에어프로덕트코리아"),
    ("SUP-D01", "태성메탈"),
    ("SUP-D02", "글로벌타겟"),
)

INSPECTION_ITEMS = {
    "Raw Wafer": "평탄도/저항률/외관",
    "Chemical": "순도/입도/비중",
    "Gas": "순도/수분/파티클",
    "Metal": "조성비/밀도/외관",
    "Mask": "CD 정확도/결함수/투과율",
    "Package": "치수/접합강도/외관",
}

ROOT_CAUSE_CATEGORIES = ("설비", "자재", "작업방법", "환경", "측정")

OWNER_DEPTS = ("공정기술팀", "설비기술팀", "자재구매팀", "품질보증팀", "생산관리팀")

OWNER_NAMES = ("권도현", "남유진", "석민재", "천보람", "표현우")

APPROVER_NAMES = ("구자현", "명수린", "봉태식", "설유나", "탁현빈")
