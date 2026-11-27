# 설계안 — 계보 기반 불량 원인 추적 시스템

명세: `SPEC_불량원인추적.md`. 이 문서는 명세 확인 후 합의한 설계와 결정 사항을 기록한다.

## 1. 디렉터리 구조

```
backend/
  app/
    main.py            FastAPI 앱, 라우터 등록, frontend/dist 정적 서빙
    config.py          설정 파일(config.toml) + 환경변수(BIF_*)
    db.py              엔진/세션. SQLite ↔ PostgreSQL 은 DATABASE_URL 만 교체
    models/            master, transaction, defect, system
    schemas/           Pydantic v2 입출력 모델
    api/               라우터
    services/          DB 조회 + core 호출 조립, AuditLog 기록
    core/              웹/DB 와 무관한 순수 모듈 (pytest 대상)
      defaults.py      업무 설정 기본값(넬슨룰, 가중치, ARIMA 등) — 매직 넘버는 여기에만
      genealogy/dag.py 깊이 제한 BFS, 순환 감지
      spc/             넬슨룰, 관리도 (Phase 2)
      forecast/        ARIMA (Phase 4)
      causes/          원인 점수 (Phase 3)
    auth/              AuthProvider 인터페이스 + 로컬 계정 구현
    llm/               Ollama 연동 (Phase 5)
    ingest/            업로드/수집 공통 검증 (Phase 6)
    jobs/              APScheduler 배치
    sample/            시드 고정 샘플 데이터 생성기
  alembic/             마이그레이션
  tests/               unit/, integration/
frontend/              React + TS + Vite (Phase 2~)
config/                config.example.toml
scripts/               외부 호출 점검, 오프라인 번들
docs/                  설계, 오프라인 설치
```

## 2. 데이터 모델

**마스터:** ItemType, ProcessStep, Equipment(x/y/z, model_path nullable), ConditionDefinition,
Characteristic(ItemType XOR ProcessStep, rule_config JSON), ProductDesign + ProductDesignComponent + ProductDesignStep, DefectType

**트랜잭션:** Lot, GenealogyEdge(DAG, 순환 거부), ProcessRun, ConditionValue,
Measurement(LOT 또는 ProcessRun, 인덱스 (characteristic_id, measured_at)), ControlLimit(버전, 고정), SpcViolation, ForecastResult

**불량/피드백:** DefectJudgment, RootCauseConfirmation, CandidateSnapshot(판정 시점 후보 — 정확도 기준), AuditLog

**시스템:** User(role: viewer/operator/admin), Setting(key → JSON), MappingTemplate

그래프 노드 ID 규칙: `lot:<id>`, `step:<id>`, `eq:<id>`, `cond:<id>` — API, 프론트, LLM 도구 공통.

시각 값은 공장 로컬 시각(타임존 없는 값)으로 저장한다.

## 3. API (접두 `/api`)

| 영역 | 엔드포인트 |
|---|---|
| 인증 | `POST /auth/login`, `GET /auth/me`, `GET/POST /users` |
| 마스터 | `CRUD /item-types /process-steps /equipment /condition-definitions /characteristics /product-designs /defect-types` |
| LOT/계보 | `GET /lots`, `GET /lots/{lot_no}`, `/runs`, `/measurements`, `/genealogy`, `POST /lots`, `POST /genealogy/edges` |
| 측정 | `GET /measurements?characteristic_id=&from=&to=` |
| 추적 | `GET /trace/upstream/{lot_no}`, `GET /trace/downstream/{node_id}` (Phase 3) |
| 원인 | `GET /causes/{lot_no}`, `POST /lots/bulk-status` (Phase 3) |
| 불량 | `POST /defects`, `GET /defects`, `POST /defects/{id}/root-cause` (Phase 3) |
| SPC | `GET /spc/{char_id}/chart`, `POST /spc/{char_id}/limits`, `GET /spc/violations`, `POST /spc/violations/{id}/ack` (Phase 2) |
| 예측 | `GET /forecast/watchlist`, `GET /forecast/{char_id}`, `POST /forecast/run` (Phase 4) |
| 정확도 | `GET /accuracy` (Phase 6) |
| 업로드/수집 | `POST /upload/preview`, `POST /upload/commit`, `POST /ingest/...` (Phase 6) |
| 어시스턴트 | `POST /assistant/chat`, `GET /assistant/health` (Phase 5) |
| 설정 | `GET /settings`, `GET/PUT /settings/{key}` |
| 이벤트 | `GET /events?from=&to=` (3D/타임라인 공용, Phase 6) |

## 4. 합의된 결정 사항

1. 인증 계층과 AuditLog 는 Phase 1 에 넣고, 사용자 관리 화면과 권한 UI 마무리는 Phase 6.
2. 설비/공정 노드 정방향 추적은 원인 run 시각 기준 ±N일(설정, 기본 7일). LOT 노드는 기간 제한 없음.
3. (b) 공유 불량률 모집단은 분석 기간 내 "완제품" 분류 LOT, DefectJudgment 가 있으면 불량.
4. 정확도 Top-k 는 판정 저장 시점의 CandidateSnapshot 기준.
5. 보류/격리 일괄 처리는 실무자 이상, 격리 해제는 관리자.
6. 서브그룹은 (특성, subgroup_no) 가 같은 측정값 묶음. 툴팁에 포함 LOT 전부 표시.
7. 판정 저장 시 LOT 상태 자동 "불량". 판정 취소는 관리자만, AuditLog.
8. 폰트는 Pretendard(OFL) woff2 를 빌드 산출물에 포함.

## 5. Phase 계획

| Phase | 내용 | 완료 기준 |
|---|---|---|
| 1 | 골격, 설정, 전체 DB 모델, Alembic, 인증 계층, AuditLog, 마스터 CRUD, LOT/계보/측정 조회, 샘플 생성기 | 샘플 생성 후 API 로 LOT/계보/측정 조회, 순환 계보 거부 |
| 2 | 넬슨룰 8종, 관리도 3종, 기준 구간 고정, 위반 저장, 프론트 골격 + 관리도 화면 | 룰별 단위 테스트, 심은 이상이 위반으로 표시 |
| 3 | 역/정방향 추적, 원인 점수 (a)~(f), 그래프 화면, 판정, 원인 확정, 격리 | 심은 원자재 LOT 또는 설비가 Top-3 (통합 테스트) |
| 4 | ARIMA, 위험 점수, 스케줄러, 주의 부품 | 추세 심은 특성 상위, 데이터 부족/적합 실패 무오류 |
| 5 | LLM 도구 함수, Ollama, 근거 링크, 호출 내역 | 예시 질문에 도구 결과 기반 답변 |
| 6 | 업로드/수집, 권한 UI, 정확도, 3D 자리, 오프라인 문서 | README 만으로 오프라인 재현 |
