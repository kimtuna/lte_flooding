# LTE Flooding

USRP 장치를 사용하여 srsRAN eNB에 연결 요청을 반복적으로 전송하는 도구입니다.

## USRP 장치 확인

```bash
uhd_find_devices
```

출력에서 `serial:` 뒤의 값이 시리얼 번호입니다.

## 사용법

### 기본 사용 (PLMN만 지정)

```bash
# PLMN만 지정 (핸드폰에 "123456"으로 표시되는 eNB)
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL" --mcc 123 --mnc 456
```

PLMN만 지정하면 srsUE가 자동으로 모든 주파수를 스캔하여 해당 PLMN의 eNB를 찾습니다.

### 주파수도 함께 지정 (더 빠름)

```bash
# PLMN + 주파수 모두 지정
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL" --mcc 123 --mnc 456 --earfcn 3400
```

### 연결 시도 간격 조정

```bash
# 연결 시도 간격 조정 (기본값: 0.1초)
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL" --mcc 123 --mnc 456 --interval 0.05
```

## 옵션

- `--usrp-args`: USRP 장치 인자 (필수, 예: `serial=30AD123` 또는 `type=b200`)
- `--mcc`: Mobile Country Code (필수, 예: 123)
- `--mnc`: Mobile Network Code (필수, 예: 456)
- `--earfcn`: 주파수 채널 번호 (선택, 지정하지 않으면 자동 스캔)
- `--interval`: 연결 시도 간격(초) (기본값: 0.1)

## 동작 방식

스크립트는 매번 다른 IMSI/IMEI를 생성하여 연결 시도를 반복합니다. 각 시도마다 새로운 "핸드폰"처럼 보이도록 고유한 식별자를 사용합니다.


