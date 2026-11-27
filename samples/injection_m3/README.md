# 사출기 3호 낮 조 공정 기록 (가상 자료)

출처: 문제 02 자료 "사출기 3호 낮 조 공정 기록" (2026-03-04 ~ 03-06, 08:00~16:00, 1시간에 한 줄, 가상 자료)를 그대로 옮겨 적은 것입니다.
밤 조 기록은 없습니다. 날짜별 합계(03-04 2,354개 중 40개, 03-05 2,331개 중 187개, 03-06 2,100개 중 50개)가 원본과 일치합니다.

- `hourly_2026-03-04_06.csv` : 원본 표 (03-06 09:00 은 사출기 정지 행)
- `mapping.json` : 열 역할 정의 (시각, 작업자, 측정값/설정값, 생산·불량 수, 최다 불량 유형, 정지 행, 데이터 주의사항)

가져오기:

```powershell
cd backend
.venv\Scripts\python -m app.cli seed-process-log --reset --file ..\samples\injection_m3\hourly_2026-03-04_06.csv --mapping ..\samples\injection_m3\mapping.json
```

다른 양식의 집계 기록은 `mapping.json` 만 새로 쓰면 같은 방식으로 가져오고 분석합니다.
