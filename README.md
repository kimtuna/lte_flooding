# LTE Flooding & Scanner

USRP 장치를 사용하여 srsRAN eNB를 탐지하고 연결 요청을 반복적으로 전송하는 도구입니다.

## USRP 장치 확인

```bash
uhd_find_devices
```

출력에서 `serial:` 뒤의 값이 시리얼 번호입니다.

## 사용법

### 1. 주변 eNB 스캔

```bash
# 기본 스캔 (30초)
python3 lte_scanner.py --usrp-args "serial=YOUR_USRP_SERIAL"

# 특정 주파수만 스캔
python3 lte_scanner.py --usrp-args "serial=YOUR_USRP_SERIAL" --earfcn 3400

# 스캔 시간 조정
python3 lte_scanner.py --usrp-args "serial=YOUR_USRP_SERIAL" --duration 60

# 인터랙티브 모드 (스캔 후 선택해서 flooding 시작)
python3 lte_scanner.py --usrp-args "serial=YOUR_USRP_SERIAL" --interactive
```

### 2. LTE Flooding

```bash
# 기본 (주파수 3400)
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL"

# MCC/MNC 지정 (핸드폰에 "123456"으로 표시되는 eNB)
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL" --mcc 123 --mnc 456

# 주파수만 지정
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL" --earfcn 3400

# MCC/MNC + 주파수 모두 지정
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL" --mcc 123 --mnc 456 --earfcn 3400

# 인스턴스 수 조정 (기본값: 10)
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL" --instances 20

# 간격 조정 (기본값: 0.1초)
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL" --interval 0.05
```

## 옵션

### lte_scanner.py
- `--usrp-args`: USRP 장치 인자 (필수, 예: `serial=30AD123` 또는 `type=b200`)
- `--earfcn`: 주파수 채널 번호 (선택)
- `--duration`: 스캔 지속 시간(초) (기본값: 30)
- `--output`: 결과 JSON 파일 경로 (기본값: 자동 생성)
- `--interactive`: 스캔 후 eNB 선택하여 flooding 시작
- `--verbose`: 상세 로그

### lte_flooding.py
- `--usrp-args`: USRP 장치 인자 (필수)
- `--mcc`: Mobile Country Code (예: 123)
- `--mnc`: Mobile Network Code (예: 456)
- `--earfcn`: 주파수 채널 번호 (기본값: 3400)
- `--instances`: 동시 실행할 srsUE 인스턴스 수 (기본값: 10)
- `--interval`: 연결 시도 간격(초) (기본값: 0.1)

