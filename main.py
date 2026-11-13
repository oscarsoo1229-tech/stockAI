import sys
import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (QApplication, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QProgressBar, QPushButton, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
    QTableWidget, QTableWidgetItem, QSpinBox)
import pyqtgraph as pg

from stockai.data import cache_frame, closed_daily, indicators, load, recommendation
from stockai.learning import MODELS, Settings, ensemble, train
from stockai.backtest import simulate


class Worker(QObject):
    progress = Signal(object)
    done = Signal(object)
    error = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(self.progress.emit))
        except Exception as exc:
            self.error.emit(str(exc))


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("StockAI | 주식 분석")
        self.resize(1450, 900)
        self.results, self.bars = {}, None
        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        self.dashboard = QWidget()
        tabs.addTab(self.dashboard, "시장 분석")
        self._dashboard()
        self.analysis = QTextEdit()
        self.analysis.setReadOnly(True)
        tabs.addTab(self.analysis, "AI 분석")
        self.train_page = QWidget()
        tabs.addTab(self.train_page, "학습 및 모델 비교")
        self._training()
        self.backtest_page = QWidget()
        tabs.addTab(self.backtest_page, "백테스트 / 가상 계좌")
        self._backtest()
        self.settings_page = QWidget()
        tabs.addTab(self.settings_page, "API 설정")
        self._settings()
        self.statusBar().showMessage("분석 전용 모드 · 실제 주문 기능 꺼짐")
        self.setStyleSheet("""
        QMainWindow,QWidget {background:#101722;color:#e6edf6;font:13px 'Segoe UI';}
        QLineEdit,QComboBox,QTextEdit,QDoubleSpinBox {background:#182434;border:1px solid #34475c;padding:7px;border-radius:5px;}
        QPushButton {background:#2368c4;border:0;padding:9px 15px;border-radius:5px;font-weight:bold;}
        QPushButton:hover {background:#327ce0;}
        QTabBar::tab {padding:12px 24px;background:#182434;} QTabBar::tab:selected {background:#2368c4;}
        """)

    def _dashboard(self):
        root = QVBoxLayout(self.dashboard)
        controls = QHBoxLayout()
        self.market = QComboBox()
        self.market.addItems(["KOSPI", "KOSDAQ", "NASDAQ", "NYSE"])
        self.symbol = QLineEdit("005930")
        self.source = QComboBox()
        self.source.addItem("가상 데이터", "Mock")
        self.source.addItem("CSV 파일", "CSV")
        self.source.addItem("Yahoo Finance", "Yahoo Finance")
        self.source.addItem("FinanceDataReader", "FinanceDataReader")
        self.source.addItem("토스증권 Open API", "Toss API")
        self.browse = QPushButton("CSV 선택")
        self.browse.clicked.connect(self.pick_csv)
        self.interval = QComboBox()
        self.interval.addItems(["일봉", "1분봉"])
        self.fetch = QPushButton("시세 불러오기")
        self.fetch.clicked.connect(self.fetch_data)
        for label, widget in [("시장", self.market), ("종목 코드 / 티커", self.symbol),
                              ("데이터 출처", self.source), ("봉 주기", self.interval)]:
            controls.addWidget(QLabel(label)); controls.addWidget(widget)
        controls.addWidget(self.browse); controls.addWidget(self.fetch)
        root.addLayout(controls)
        self.status = QLabel("가상 데이터를 선택하거나 파일·증권사 API를 연결하세요.")
        root.addWidget(self.status)
        self.chart = pg.PlotWidget()
        self.chart.setBackground("#101722")
        self.chart.showGrid(x=True, y=True, alpha=.2)
        root.addWidget(self.chart, 1)
        self.indicator_text = QTextEdit()
        self.indicator_text.setMaximumHeight(120)
        root.addWidget(self.indicator_text)
        self.opinion = QLabel("분석 의견은 시세를 불러온 뒤 표시됩니다.")
        self.opinion.setWordWrap(True)
        root.addWidget(self.opinion)
        scan_row = QHBoxLayout()
        self.scan_button = QPushButton("선택 시장 종목 분석")
        self.scan_button.clicked.connect(self.scan_market)
        self.scan_limit = QSpinBox()
        self.scan_limit.setRange(10, 100)
        self.scan_limit.setValue(30)
        self.scan_offset = QSpinBox()
        self.scan_offset.setRange(0, 100000)
        self.scan_offset.setSingleStep(30)
        self.scan_offset.setPrefix("목록 시작 위치 ")
        self.min_price = QDoubleSpinBox()
        self.min_price.setRange(0, 1e12)
        self.min_price.setDecimals(2)
        self.min_price.setValue(1000)
        self.min_turnover = QDoubleSpinBox()
        self.min_turnover.setRange(0, 1e15)
        self.min_turnover.setDecimals(0)
        self.min_turnover.setValue(1_000_000_000)
        self.market.currentTextChanged.connect(self.set_default_filters)
        scan_row.addWidget(self.scan_button)
        scan_row.addWidget(self.scan_offset)
        scan_row.addWidget(QLabel("이번 분석 종목 수"))
        scan_row.addWidget(self.scan_limit)
        scan_row.addStretch(1)
        root.addLayout(scan_row)
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("최저 주가"))
        filter_row.addWidget(self.min_price)
        filter_row.addWidget(QLabel("20봉 평균 거래대금 하한"))
        filter_row.addWidget(self.min_turnover)
        root.addLayout(filter_row)
        self.scan_progress = QProgressBar()
        root.addWidget(self.scan_progress)
        self.scan_table = QTableWidget(0, 6)
        self.scan_table.setHorizontalHeaderLabels(["종목 코드", "종목명", "분석 의견", "점수", "20봉 수익률", "거래량 배수"])
        root.addWidget(self.scan_table)
        self.market.currentTextChanged.connect(self.default_symbol)
        self.source.currentTextChanged.connect(lambda name: self.browse.setEnabled(self.source.currentData() == "CSV"))

    def default_symbol(self, market):
        self.symbol.setText("005930" if market.startswith("KOS") else "AAPL")

    def set_default_filters(self, market):
        if not hasattr(self, "min_price"):
            return
        if market.startswith("KOS"):
            self.min_price.setValue(1000)
            self.min_turnover.setValue(1_000_000_000)
        else:
            self.min_price.setValue(5)
            self.min_turnover.setValue(1_000_000)

    def pick_csv(self):
        path, _ = QFileDialog.getOpenFileName(self, "OHLCV CSV 열기", "", "CSV (*.csv)")
        if path:
            self.csv_path = path
            self.status.setText(Path(path).name)

    def fetch_data(self):
        try:
            provider = self.source.currentData()
            if provider == "Toss API":
                from stockai.brokers import TossBroker
                import keyring
                client_id = keyring.get_password("StockAI", "toss_client_id") or os.environ.get("STOCKAI_TOSS_CLIENT_ID", "")
                client_secret = keyring.get_password("StockAI", "toss_client_secret") or os.environ.get("STOCKAI_TOSS_CLIENT_SECRET", "")
                client = TossBroker(client_id, client_secret)
                if not client.client_id or not client.client_secret:
                    raise ValueError("Windows 사용자 환경 변수 STOCKAI_TOSS_CLIENT_ID와 STOCKAI_TOSS_CLIENT_SECRET을 먼저 설정하세요.")
                candles, _ = client.candles(self.symbol.text().strip(),
                                           "1m" if self.interval.currentIndex() else "1d")
                import pandas as pd
                frame = pd.DataFrame(candles)
                frame = frame.rename(columns={"openPrice":"open","highPrice":"high","lowPrice":"low",
                                              "closePrice":"close"}).set_index("timestamp")
                self.bars = cache_frame(provider, self.market.currentText(), self.symbol.text(), self.interval.currentText(), frame)
                client.close()
            else:
                frame = load(provider, self.market.currentText(), self.symbol.text().strip(),
                             getattr(self, "csv_path", None))
                self.bars = cache_frame(provider, self.market.currentText(), self.symbol.text(), self.interval.currentText(), frame)
            self.bars = closed_daily(self.bars, self.market.currentText()) if self.interval.currentIndex() == 0 else self.bars
            self.chart.clear()
            self.chart.plot(range(len(self.bars)), self.bars.close.to_numpy(), pen=pg.mkPen("#4da3ff", width=2))
            tech = indicators(self.bars).iloc[-1]
            self.indicator_text.setPlainText(" · ".join(
                f"{key} {value:,.3f}" for key, value in tech.items() if str(value) != "nan"))
            view = recommendation(self.bars)
            self.opinion.setText(
                f"기술적 분석 의견: {view['label']} · 점수 {view['score']:+d} · "
                f"20봉 수익률 {view['return_20d']:+.2%} · RSI {view['rsi']:.1f} · "
                f"평균 대비 거래량 {view['volume_ratio']:.2f}배\n"
                f"판단 근거: " + " · ".join(f"{name} {score:+d}" for name, score in view["factors"].items()))
            source_name = {"Mock": "가상 데이터", "CSV": "CSV 파일", "Toss API": "토스증권 Open API"}.get(provider, provider)
            self.status.setText(f"{source_name} · {len(self.bars)}개 봉 · 기준 시각 {self.bars.index[-1]}")
        except Exception as exc:
            QMessageBox.critical(self, "데이터 오류", str(exc))

    def scan_market(self):
        if self.source.currentData() != "Toss API":
            return QMessageBox.information(self, "시장 전체 분석", "시장 전체 종목 조회에는 토스증권 Open API를 선택하세요.")
        import keyring
        from stockai.brokers import TossBroker, extract_symbols
        client_id = keyring.get_password("StockAI", "toss_client_id") or os.environ.get("STOCKAI_TOSS_CLIENT_ID", "")
        client_secret = keyring.get_password("StockAI", "toss_client_secret") or os.environ.get("STOCKAI_TOSS_CLIENT_SECRET", "")
        if not client_id or not client_secret:
            return QMessageBox.warning(self, "인증 정보 없음", "API 설정 탭에서 토스증권 인증 정보를 먼저 저장하세요.")
        market = self.market.currentText()
        limit = self.scan_limit.value()
        offset = self.scan_offset.value()
        minimum_price = self.min_price.value()
        minimum_turnover = self.min_turnover.value()
        self.scan_button.setEnabled(False)
        self.scan_table.setRowCount(0)
        self.launch(lambda progress: self._scan(client_id, client_secret, market, limit, offset,
                                                minimum_price, minimum_turnover, progress), self.scan_done)

    @staticmethod
    def _scan(client_id, client_secret, market, limit, offset, minimum_price, minimum_turnover, progress):
        import pandas as pd
        from stockai.brokers import TossBroker, extract_symbols
        from stockai.data import normalize, recommendation
        broker = TossBroker(client_id, client_secret)
        try:
            symbols = extract_symbols(broker.stocks(market))
            if not symbols:
                raise ValueError("종목 목록에서 심볼을 읽지 못했습니다. 토스 API 응답을 확인하세요.")
            symbols = symbols[offset:offset + limit]
            if not symbols:
                raise ValueError("시장 종목 목록의 끝에 도달했습니다. 시작 위치를 낮춰 주세요.")
            rows = []
            excluded_price = 0
            excluded_turnover = 0
            for index, (symbol, name) in enumerate(symbols, 1):
                progress({"scan": index, "total": len(symbols), "symbol": symbol})
                try:
                    candles, _ = broker.candles(symbol, "1d", 60)
                    frame = pd.DataFrame(candles).rename(columns={
                        "openPrice": "open", "highPrice": "high", "lowPrice": "low", "closePrice": "close"
                    }).set_index("timestamp")
                    frame = normalize(frame)
                    if frame.close.iloc[-1] < minimum_price:
                        excluded_price += 1
                        continue
                    average_turnover = float((frame.close.tail(20) * frame.volume.tail(20)).mean())
                    if average_turnover < minimum_turnover:
                        excluded_turnover += 1
                        continue
                    view = recommendation(frame)
                    rows.append({"symbol": symbol, "name": name, **view})
                except Exception:
                    continue
                finally:
                    time.sleep(.06)
            return {"rows": sorted(rows, key=lambda row: (row["score"], row["volume_ratio"]), reverse=True),
                    "checked": len(symbols), "excluded_price": excluded_price,
                    "excluded_turnover": excluded_turnover}
        finally:
            broker.close()

    def scan_done(self, result):
        rows = result["rows"]
        self.scan_button.setEnabled(True)
        self.scan_progress.setValue(self.scan_progress.maximum())
        self.scan_table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            values = [row["symbol"], row["name"], row["label"], str(row["score"]),
                      f"{row['return_20d']:+.2%}", f"{row['volume_ratio']:.2f}배"]
            for column, value in enumerate(values):
                self.scan_table.setItem(row_index, column, QTableWidgetItem(value))
        self.status.setText(
            f"종목 {result['checked']}개 조회 · 기준 미달 제외 {result['excluded_price'] + result['excluded_turnover']}개 · "
            f"분석 표시 {len(rows)}개 · 참고용 기술 지표")

    def _training(self):
        root = QVBoxLayout(self.train_page)
        form = QFormLayout()
        self.models = QComboBox()
        model_labels = {
            "Random Forest": "랜덤 포레스트",
            "XGBoost": "XGBoost",
            "LSTM": "LSTM",
            "GRU": "GRU",
            "Transformer": "트랜스포머",
        }
        for model in MODELS:
            self.models.addItem(model_labels[model], model)
        self.epochs = QComboBox()
        for value in (5, 10, 20, 40):
            self.epochs.addItem(f"{value}회", value)
        self.batch = QComboBox()
        for value in (16, 32, 64):
            self.batch.addItem(str(value), value)
        self.device = QComboBox()
        self.device.addItem("자동 선택", "Auto")
        self.device.addItem("GPU 사용", "GPU")
        self.device.addItem("CPU 사용", "CPU")
        self.trials = QComboBox()
        for value in (0, 5, 10):
            self.trials.addItem(f"{value}회", value)
        form.addRow("모델", self.models); form.addRow("학습 반복 횟수", self.epochs)
        form.addRow("묶음 크기", self.batch); form.addRow("장치", self.device)
        form.addRow("자동 조정 횟수", self.trials)
        root.addLayout(form)
        self.progress = QProgressBar()
        root.addWidget(self.progress)
        self.train_button = QPushButton("학습 및 검증 시작")
        self.train_button.clicked.connect(self.start_training)
        root.addWidget(self.train_button)
        self.train_log = QTextEdit(); self.train_log.setReadOnly(True)
        root.addWidget(self.train_log, 1)
        self.compare = QPushButton("학습 완료 모델 앙상블 비교")
        self.compare.clicked.connect(self.compare_models)
        root.addWidget(self.compare)

    def _settings(self):
        root = QVBoxLayout(self.settings_page)
        form = QFormLayout()
        self.client_id = QLineEdit()
        self.client_secret = QLineEdit()
        self.client_secret.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("토스증권 Client ID", self.client_id)
        form.addRow("토스증권 Client Secret", self.client_secret)
        root.addLayout(form)
        buttons = QHBoxLayout()
        save = QPushButton("인증 정보 안전하게 저장")
        save.clicked.connect(self.save_credentials)
        check = QPushButton("토스 API 연결 확인")
        check.clicked.connect(self.check_broker)
        buttons.addWidget(save)
        buttons.addWidget(check)
        root.addLayout(buttons)
        self.api_status = QLabel("인증 정보는 Windows 자격 증명 저장소에 보관합니다.")
        root.addWidget(self.api_status)
        root.addWidget(QLabel("시세 조회와 계좌 조회는 증권사에서 부여한 권한 및 허용 IP 설정에 따라 달라집니다."))
        root.addStretch(1)

    def save_credentials(self):
        if not self.client_id.text().strip() or not self.client_secret.text():
            return QMessageBox.warning(self, "입력 확인", "Client ID와 Client Secret을 모두 입력하세요.")
        try:
            import keyring
            keyring.set_password("StockAI", "toss_client_id", self.client_id.text().strip())
            keyring.set_password("StockAI", "toss_client_secret", self.client_secret.text())
            self.api_status.setText("인증 정보를 Windows 자격 증명 저장소에 저장했습니다.")
            self.client_secret.clear()
        except Exception as exc:
            QMessageBox.critical(self, "저장 실패", f"Windows 자격 증명 저장소에 저장하지 못했습니다. {exc}")

    def check_broker(self):
        try:
            import keyring
            from stockai.brokers import TossBroker
            client_id = self.client_id.text().strip() or keyring.get_password("StockAI", "toss_client_id")
            client_secret = self.client_secret.text() or keyring.get_password("StockAI", "toss_client_secret")
            if not client_id or not client_secret:
                raise ValueError("먼저 토스증권 인증 정보를 저장하세요.")
            broker = TossBroker(client_id, client_secret)
            accounts = broker.accounts()
            broker.close()
            self.api_status.setText(f"토스 API 연결 성공 · 계좌 {len(accounts.get('accounts', []))}개 확인")
        except Exception as exc:
            self.api_status.setText(f"연결 확인 실패 · {exc}")

    def start_training(self):
        if self.bars is None:
            return QMessageBox.information(self, "시세 없음", "먼저 시세를 불러오세요.")
        self.train_button.setEnabled(False)
        settings = Settings(epochs=self.epochs.currentData(), batch=self.batch.currentData(),
                            device=self.device.currentData(), trials=self.trials.currentData())
        self.launch(lambda progress: train(self.bars, self.models.currentData(), settings,
                                           Path.home()/"StockAI"/"models", progress), self.training_done)

    def launch(self, fn, done):
        self.thread = QThread()
        self.worker = Worker(fn)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.report)
        self.worker.done.connect(done)
        self.worker.error.connect(self.fail)
        self.worker.done.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)
        self.thread.start()

    def report(self, info):
        if isinstance(info, dict) and info.get("scan"):
            self.scan_progress.setMaximum(info["total"])
            self.scan_progress.setValue(info["scan"])
            self.status.setText(f"시장 종목 분석 중 {info['scan']}/{info['total']} · {info['symbol']}")
            return
        if isinstance(info, dict):
            phases = {"Optuna": "자동 설정 탐색", "Final": "최종 모델 학습"}
            line = phases.get(info.get("phase"), f"검증 구간 {info.get('phase', '')}")
            if info.get("epoch"):
                line += (f" · {info['epoch']}/{info['epochs']}회 · "
                         f"학습 손실 {info['train_loss']:.4f} · 검증 손실 {info['val_loss']:.4f}")
                if info.get("gpu"):
                    line += f" · GPU {info['gpu']} · 메모리 {info['gpu_mib']}MiB"
                else:
                    line += " · CPU"
            self.train_log.append(line)
        else:
            self.train_log.append(str(info))
        if isinstance(info, dict) and info.get("epoch"):
            self.progress.setMaximum(info["epochs"])
            self.progress.setValue(info["epoch"])

    def training_done(self, result):
        self.results[result["model"]] = result
        self.train_button.setEnabled(True)
        model_name = {"Random Forest": "랜덤 포레스트", "Transformer": "트랜스포머"}.get(result["model"], result["model"])
        metric_names = {"accuracy": "정확도", "balanced_accuracy": "균형 정확도", "precision": "정밀도",
                        "recall": "재현율", "f1": "F1 점수", "auc": "ROC-AUC", "brier": "브라이어 점수",
                        "return_mae": "수익률 평균절대오차", "return_rmse": "수익률 제곱근평균제곱오차",
                        "price_mae": "가격 평균절대오차", "price_rmse": "가격 제곱근평균제곱오차",
                        "directional_accuracy": "방향 적중률"}
        metric_text = "\n".join(f"{h}거래일: " + " · ".join(
            f"{metric_names.get(key, key)} {value:.4f}" for key, value in values.items()
            if key != "roc" and value is not None) for h, values in result["metrics"].items())
        self.analysis.setPlainText(
            f"{model_name} · 기준 시각 {result['asof']}\n"
            f"수익률 추정치는 모델 출력입니다.\n" +
            "\n".join(f"{h}거래일 상승확률 {p:.1%} · 예상수익률 {r:+.2%} · 예상가격 {price:,.2f} · "
                      f"90% 오차구간 ±{band:.2%}"
                      for h, p, r, price, band in zip((1,5,20), result["probability"],
                                                       result["return"], result["price"], result["residual_band"])) +
            f"\n\n기간 순 검증 지표\n{metric_text}")
        self.statusBar().showMessage("학습 및 기간 순 검증을 완료했습니다.")

    def fail(self, message):
        self.train_button.setEnabled(True)
        self.scan_button.setEnabled(True)
        QMessageBox.critical(self, "작업 오류", message)

    def compare_models(self):
        if len(self.results) < 2:
            return QMessageBox.information(self, "비교", "같은 시세 데이터로 모델을 두 개 이상 학습하세요.")
        try:
            combined = ensemble(list(self.results.values()))
            names = ("1일", "5일", "20일")
            self.analysis.setPlainText("모델 앙상블 결과 · 같은 검증 구간 단순 평균\n" + "\n".join(
                f"{h} 상승 확률 {p:.1%} · 예상 수익률 {r:+.2%} · 예상 가격 {price:,.2f}"
                for h, p, r, price in zip(names, combined["probability"], combined["return"], combined["price"])))
        except Exception as exc:
            self.fail(str(exc))

    def _backtest(self):
        root = QVBoxLayout(self.backtest_page)
        form = QFormLayout()
        self.cash = QDoubleSpinBox(); self.cash.setRange(100, 1e10); self.cash.setValue(10000)
        self.cost = QDoubleSpinBox(); self.cost.setRange(0, 300); self.cost.setValue(10)
        form.addRow("초기 자금", self.cash); form.addRow("매매 비용 (bp)", self.cost)
        root.addLayout(form)
        button = QPushButton("최근 검증 신호로 모의 백테스트")
        button.clicked.connect(self.run_backtest)
        root.addWidget(button)
        self.backtest_result = QTextEdit(); self.backtest_result.setReadOnly(True)
        root.addWidget(self.backtest_result)

    def run_backtest(self):
        if not self.results or self.bars is None:
            return self.fail("먼저 모델 학습과 검증을 실행하세요.")
        result = next(reversed(self.results.values()))
        import pandas as pd
        p = pd.Series([row[0] for row in result["oos_p"]], index=pd.to_datetime(result["dates"]))
        curve, trades, metrics = simulate(self.bars, p, self.cash.value(), self.cost.value(), 5)
        self.backtest_result.setPlainText(f"매매 비용 {self.cost.value()}bp + 미끄러짐 비용 5bp · 다음 거래일 시가 체결 가정\n{metrics}\n체결 수 {len(trades)}")
        self.analysis.append("\n백테스트는 다음 시가 체결과 비용 가정을 사용합니다.")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Window()
    window.show()
    sys.exit(app.exec())
