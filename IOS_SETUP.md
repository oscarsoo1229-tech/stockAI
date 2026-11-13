# StockAI 모바일 실행 안내

## Windows에서 과제 시연

압축을 풀고 StockAI 폴더에서 `start_mobile_demo.bat`을 실행합니다. Python 3.13이 필요하며 첫 실행에서는 모바일 모듈을 설치합니다. Windows용 GUI와 별도로 실행할 수 있습니다. 명령 프롬프트에서 직접 실행하려면 아래 명령을 사용합니다.

```bat
py -3.13 -m venv .venv-mobile
.venv-mobile\Scripts\python -m pip install -r requirements_mobile.txt
set "STOCKAI_PASSWORD=StockAI-Demo-Local-1234"
.venv-mobile\Scripts\python -m uvicorn mobile.server:app --host 127.0.0.1 --port 8765
```

브라우저에서 http://127.0.0.1:8765 를 열고 사용자 이름 `stockai`와 위 비밀번호를 입력합니다. **다음 모의 봉**을 누르거나 8초마다 자동으로 한 봉을 진행합니다. 종가·거래량 차트, RSI, 20봉 수익률, 거래량 배수와 기술 점수가 함께 갱신됩니다. 새 모의 뉴스와 조건에 맞는 신호 변경은 최근 알림에 기록됩니다. 모의 날짜와 가격은 재현 가능한 합성 시계열입니다.

과제 시연은 데이터 생성·검증에 NumPy와 pandas, 웹 서버에 FastAPI·Uvicorn, API 호출에 HTTPX, 푸시 발송에 pywebpush를 쓰는 흐름으로 설명할 수 있습니다.

## 아이폰에서 설치 및 푸시

1. Python 호스팅에서 아래 명령으로 설치·실행합니다. SQLite 파일은 지속 디스크에 두고 단일 워커로 실행합니다. 호스팅이 잠들면 모니터링도 멈춥니다.

   ```sh
   pip install -r requirements_mobile.txt
   uvicorn mobile.server:app --host 0.0.0.0 --port "${PORT:-8765}"
   ```

2. 호스팅 환경 변수에 외부용 `STOCKAI_PASSWORD`를 강한 별도 비밀번호로 지정하고, `STOCKAI_DB`에 지속 디스크의 SQLite 파일 경로를 지정합니다. Python 환경에서 `python -m mobile.generate_keys`를 한 번 실행해 VAPID 키를 생성합니다. 출력된 `VAPID_PUBLIC_KEY`와 `VAPID_PRIVATE_KEY`를 등록합니다. 개인 키 환경 변수 값은 서버 안에 비공개로 올려 둔 PEM 파일의 경로입니다. `VAPID_CONTACT`에는 `mailto:본인이메일주소`를 설정합니다. 동일한 키를 배포마다 재사용합니다.
3. iOS 16.4 이상에서 Safari로 HTTPS 주소를 열고 **공유 → 홈 화면에 추가**를 선택합니다. 홈 화면에서 StockAI를 실행하고 로그인합니다. **알림 켜기 → 허용 → 테스트 알림**으로 실제 단말 수신을 확인합니다. 최근 알림에는 조건에 맞는 이벤트가 기록됩니다. 푸시 테스트 성공 문구는 푸시 서비스의 접수 상태를 나타냅니다.

## 실제 시세·뉴스·공시 설정

### Render 상태 확인 및 자동 복구

재배포 후 Render의 **Settings → Health Check Path**에 `/healthz`를 입력합니다. 이 경로는 로그인 없이 서버의 감시 작업과 SQLite 쓰기 가능 여부를 확인합니다. 외부 시세나 뉴스 API 장애 시에는 기존 분석을 표시하고 다음 갱신 주기에 다시 조회합니다. 갱신 작업 자체가 반복해서 실패하면 재시도 간격을 점진적으로 늘립니다. 앱 상단의 서버 갱신 상태와 Render 로그에서 확인할 수 있습니다.

Render는 상태 확인에 연속으로 실패하는 실행 인스턴스를 재시작합니다. 소스 코드 수정과 배포 설정 변경은 운영자가 처리합니다. [Render Health Checks](https://render.com/docs/health-checks)

### 분석 표본을 계속 쌓는 방법

GitHub에 업데이트된 코드를 올리고 Render에서 다시 배포합니다. Render Web Service에 **Persistent Disk**를 추가하고 마운트 경로를 `/var/data`로 지정합니다. **Environment**에서 `STOCKAI_DB=/var/data/stockai-live.sqlite3`를 지정합니다. 모의 시연용 서비스에는 `/var/data/stockai-demo.sqlite3`를 사용합니다. 이미 지속 디스크에 SQLite 파일이 있다면 기존 파일 경로를 그대로 쓰세요. 서버가 시작할 때 같은 파일에 표본 테이블을 자동으로 추가합니다.

처음 조회한 일봉 이력으로 과거 표본을 채우고, 새 일봉이 들어오면 종목·날짜별로 신호와 점수를 한 번씩 저장합니다. 완료 일봉이 5개 더 쌓이면 결과와 적중 여부를 기록합니다. 결과를 평가할 때 해당 날짜부터 5봉 뒤까지의 시세가 모두 조회 범위 안에 있어야 합니다. 적중률은 같은 방향의 기록에서 결과 구간이 겹치지 않는 표본으로 다시 계산합니다. 이는 신호 평가 통계의 자동 갱신이며 모델 재학습은 포함하지 않습니다.

아이폰이나 PC에서 로그인 후 **분석 표본 CSV 다운로드**를 누르면 누적 기록을 받을 수 있습니다. `source`의 `backfill`은 과거 시세로 재구성한 신호, `observed`는 조회 시점의 최신 일봉에서 기록한 신호입니다. `forward_return`은 소수 비율이며 `0.02`는 2%입니다. 관망은 방향 평가 대상에서 빠집니다. 모의 시계열이 끝까지 진행되면 마지막 봉에서 멈춥니다.

Render 무료 서비스의 로컬 디스크는 재배포나 재시작 시 초기화될 수 있습니다. 장기 누적에는 지속 디스크가 연결된 환경을 사용합니다. [Render Persistent Disks](https://render.com/docs/disks)

매수 관심과 매도 주의에는 같은 종목의 과거 동일 신호가 5봉 뒤 상승 또는 하락 방향을 맞힌 비율을 표시합니다. 결과 기간이 겹치지 않는 신호만 세며, 10건 미만이면 표본 부족으로 표시합니다. 모의 모드에서는 합성 시세의 적중률을 계산합니다. 이 수치는 미래 확률의 추정치로 보정되지 않았습니다.

서버 환경 변수에 `STOCKAI_MODE=live`, `STOCKAI_TOSS_CLIENT_ID`, `STOCKAI_TOSS_CLIENT_SECRET`을 설정합니다. 토스 WTS의 허용 IP 관리에는 분석 서버의 외부 송신 IP를 등록합니다. 기본 확인 간격은 300초이며 `STOCKAI_REFRESH_SECONDS`로 조정합니다. 신호는 완료 일봉의 기술 점수 구간이 바뀌어 매수 관심 또는 매도 주의가 됐을 때 기록합니다. 초기 조회는 비교 기준으로 사용합니다. Windows의 딥러닝 학습은 모바일 서버에서 호출하지 않습니다.

- 뉴스는 `NAVER_CLIENT_ID`와 `NAVER_CLIENT_SECRET`을 등록한 뒤 관심 종목의 회사명 또는 검색어로 수집합니다. 검색 결과 제목과 원문 링크를 표시합니다.
- 한국 공시는 `DART_API_KEY`와 종목별 DART 고유번호를 사용합니다. 미국 공시는 `SEC_USER_AGENT`에 이름과 연락용 이메일을 지정하고 종목별 CIK를 입력합니다.
- 관심 종목은 최대 20개입니다. 공급자 조회가 실패하면 이전 데이터와 갱신 실패 문구를 표시합니다. 뉴스·공시의 첫 조회는 기준으로 기록하고 후속 조회에서 새로운 항목을 알립니다.
- 모의 데이터베이스와 실제 데이터베이스에는 서로 다른 `STOCKAI_DB` 경로를 설정하세요.

참고 문서

- [Apple Web Push](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers)
- [토스증권 Open API](https://developers.tossinvest.com/)
- [네이버 뉴스 검색](https://developers.naver.com/docs/serviceapi/search/news/news.md)
- [금융감독원 Open DART](https://opendart.fss.or.kr/)
- [SEC EDGAR API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
