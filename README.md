# BUG-IS-A-FEATHER — 계보 기반 불량 원인 추적 시스템

사내망(폐쇄망) 단일 서버에서 동작하는 범용 불량 원인 추적 도구입니다.
제품 계보(DAG)를 거슬러 원인 후보를 찾고, 넬슨룰 SPC, ARIMA 추세 예측, 불량 판정 피드백,
로컬 LLM 어시스턴트를 한 흐름으로 제공합니다. 런타임에 외부 인터넷 호출이 없습니다.

- 명세: [SPEC_불량원인추적.md](SPEC_불량원인추적.md)
- 설계: [docs/DESIGN.md](docs/DESIGN.md)

## 빠른 시작 (개발 PC)

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy ..\config\config.example.toml ..\config\config.toml   # 필요 시 수정
.venv\Scripts\python -m app.cli init-db                      # 마이그레이션 + 초기 관리자
.venv\Scripts\python -m app.cli seed-sample --reset          # 샘플 시나리오
.venv\Scripts\python -m app.cli serve                        # http://localhost:8000
```

샘플 계정: `admin / admin1234`(관리자, 설정 파일 값), `operator / operator1234`(실무자), `viewer / viewer1234`(조회자)

## 프론트엔드 빌드 (인터넷 되는 PC)

```powershell
cd frontend
npm ci
npm run build      # frontend/dist 생성 → FastAPI 가 / 에서 서빙
```

개발 중에는 `npm run dev` (http://localhost:5173, /api 는 8000 으로 프록시).

## SPC 동작 요약

- 관리도 종류: 서브그룹 크기 1 → I-MR, 2~9 → Xbar-R, 10 이상 → Xbar-S (특성별 수동 지정 가능)
- 관리한계: 관리자가 **기준 구간**을 지정해 계산·고정(버전 관리, AuditLog). 고정 전에는 처음 20개 서브그룹으로 계산한 *임시 한계*를 쓴다.
- 넬슨룰: 전역 기본값(`설정 > nelson_rules`) + 특성별 덮어쓰기. 룰 8 은 넬슨 원 정의대로 "±1σ 밖 연속 n점이 중심선 양쪽에 걸침" 으로 판정한다.
- 같은 룰이 이어서 성립하면 하나의 위반으로 묶고, 처음 성립한 시점을 발생 시각으로 저장한다. 확인(ack)한 위반은 재계산해도 유지된다.
- 스케줄러가 주기적으로(기본 60분) 위반을 재계산한다.

## 테스트

```powershell
cd backend
.venv\Scripts\python -m pytest
```

## 진행 현황

| Phase | 내용 | 상태 |
|---|---|---|
| 1 | 골격, DB 모델, 마이그레이션, 인증 계층, AuditLog, 마스터 CRUD, LOT/계보/측정 조회, 샘플 생성기 | 완료 |
| 2 | 넬슨룰 8종(룰별 On/Off·파라미터), 관리도 3종 자동/수동, 기준 구간 고정, 위반 저장, 프론트 골격, 관리도 화면 | 완료 |

## 샘플 시나리오

`seed-sample` 은 시드 고정(기본 42)으로 다음 구조를 만듭니다.

원자재 LOT(A, B, C) → 혼합 배치(A 2개 + B + C 병합) → 완제품 LOT 4개(분할)

| 심어 둔 이상 | 내용 |
|---|---|
| 원자재 LOT | `RA-0016` 순도 저하 → 관련 배치 점도/제품 강도 저하, 불량률 상승 |
| 설비 | `혼합기-2` 2026-10-09 ~ 10-19 혼합 온도 허용 범위 초과 |
| 추세 | 원자재-B 수분이 시간에 따라 상승 (ARIMA 주의 부품) |
| 설계 불일치 | 배치 1건이 설계 비율과 다르게 투입 |
