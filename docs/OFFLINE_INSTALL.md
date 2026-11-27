# 폐쇄망(사내망) 오프라인 설치

런타임에 외부 인터넷 호출이 없습니다. 의존성은 **인터넷 되는 PC에서 묶어** 폐쇄망 서버로 옮겨 설치합니다.
서버 1대(Windows 또는 Linux), Docker 없이 동작합니다. Node.js 는 빌드 PC 에만 필요합니다.

## 1. 인터넷 되는 PC: 묶음 만들기

준비물: Git, Python 3.12 이상(3.13 권장, 서버와 같은 메이저·마이너 버전 권장), Node.js 20 이상, (선택) Ollama

```powershell
# 대상 서버가 Windows
powershell -ExecutionPolicy Bypass -File scripts\bundle_offline.ps1 -Model qwen2.5:7b-instruct
# 대상 서버가 Linux x86_64
powershell -ExecutionPolicy Bypass -File scripts\bundle_offline.ps1 -TargetLinux -PythonVersion 3.13 -Model qwen2.5:7b-instruct
```

만들어지는 것 (`offline_bundle.zip`):

| 항목 | 내용 |
|---|---|
| `app.zip` | 소스 (git 추적 파일) |
| `frontend_dist/` | 프론트 빌드 산출물 (라이브러리·폰트 포함, CDN 없음) |
| `wheels/` | `backend/requirements.lock` 의 모든 Python 패키지 wheel |
| `ollama_models/` | (모델 지정 시) Ollama 모델 파일 |

Ollama 설치 파일(Windows `OllamaSetup.exe`, Linux `ollama-linux-amd64.tgz`)은 ollama.com 에서 대상 OS 용을 받아 함께 넣습니다.
KAMP 데모를 쓸 경우 KAMP 원본 CSV(`casting_equipment.csv`, `casting_quality.csv`)도 별도로 옮깁니다(저장소에는 없음).

## 2. 폐쇄망 서버: 설치

```powershell
# 압축 해제 후
Expand-Archive app.zip -DestinationPath C:\bif
cd C:\bif\backend
python -m venv .venv
.venv\Scripts\pip install --no-index --find-links ..\..\wheels -r requirements.lock
xcopy /E /I ..\..\frontend_dist ..\frontend\dist
copy ..\config\config.example.toml ..\config\config.toml    # secret_key, 관리자 비밀번호, DB, LLM 모델명 수정
.venv\Scripts\python -m app.cli init-db
```

Linux 는 경로만 바꿔 같은 순서입니다 (`.venv/bin/pip install --no-index --find-links ../../wheels -r requirements.lock`).

데모 데이터:

```powershell
.venv\Scripts\python -m app.cli seed-kamp --reset --raw-dir D:\kamp\raw   # KAMP 다이캐스팅 실데이터 (권장)
.venv\Scripts\python -m app.cli seed-sample --reset                      # 합성 시나리오 (원본 없이)
```

실행:

```powershell
.venv\Scripts\python -m app.cli serve      # http://서버주소:8000  (단일 프로세스: 스케줄러 중복 방지)
```

Windows 서비스로 등록하려면 NSSM 등으로 위 명령을 등록하고, Linux 는 systemd 유닛에 `ExecStart=/opt/bif/backend/.venv/bin/python -m app.cli serve`, `WorkingDirectory=/opt/bif/backend` 를 지정합니다.

## 3. Ollama 와 모델 반입

1. 서버에 Ollama 설치 (설치 파일 실행 또는 Linux tgz 를 `/usr/local` 에 풀고 `ollama serve`).
2. 모델: 묶음의 `ollama_models` 폴더를 서버의 모델 폴더로 복사
   - Windows: `%USERPROFILE%\.ollama\models`
   - Linux: `~/.ollama/models` (서비스 계정이면 `/usr/share/ollama/.ollama/models`)
3. 또는 GGUF 파일을 반입했다면 `Modelfile` 에 `FROM ./model.gguf` 한 줄을 쓰고 `ollama create 내모델 -f Modelfile`.
4. `ollama list` 로 확인 후 `config.toml` 의 `[llm] model` 을 그 이름으로.
5. `ollama_url` 은 `http://localhost:11434` 또는 사내망 주소만 허용됩니다(외부 주소는 앱이 거부).

Ollama 가 없어도 LLM 어시스턴트 패널에 안내만 뜨고 나머지 기능은 정상 동작합니다.

## 4. PostgreSQL 로 전환 (선택)

wheel 묶음에 `psycopg[binary]` 를 추가로 받아 설치하고 `config.toml` 의 `[database] url = "postgresql+psycopg://user:pw@host:5432/bif"` 로 바꾼 뒤 `init-db` 를 실행합니다. 코드에 DB 종속 SQL 은 없습니다.

## 5. 배포 전 점검

```powershell
python scripts\check_no_external.py     # 소스·빌드 산출물·설정의 외부 네트워크 호출 점검 (위반 시 종료 코드 1)
cd backend; .venv\Scripts\python -m pytest
```

`check_no_external.py` 를 백엔드 가상환경의 Python 으로 실행하면 텔레메트리 SDK·익스포터가 설치돼 있지 않은지도 확인합니다.
(FastAPI 가 의존하는 `opentelemetry-api` 는 전송 기능이 없는 API 패키지라 설치돼 있어도 아무것도 보내지 않습니다.)

체크리스트:

- [ ] `check_no_external.py` 통과
- [ ] 브라우저 개발자도구 네트워크 탭에서 서버 주소 외 요청이 없음 (폰트·스크립트 포함)
- [ ] 초기 계정으로 처음 로그인해 비밀번호 변경 (변경 화면이 자동으로 나옴), `allow_default_passwords` 가 `false` 인지 확인
- [ ] 운영 서버는 `[live] simulator_enabled = false` (시연 기록이 실제 기록과 섞이지 않게). 시연을 돌렸다면 대시보드에서 **시연 기록 지우기**
- [ ] `data/secret.key` (자동 생성된 서명 키) 는 백업·복사 대상에서 빼거나 DB 와 같은 수준으로 보호
- [ ] `ollama_url` 이 로컬/사내망
- [ ] 서버 방화벽에서 아웃바운드 차단 상태로 데모 시나리오 재현
