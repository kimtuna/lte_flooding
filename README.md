# LTE Flooding

USRP 장치를 사용하여 srsRAN eNB에 연결 요청을 반복적으로 전송하는 도구입니다.

## 개요

이 프로젝트는 두 개의 USRP 장치를 사용합니다:
- **USRP A**: srsRAN을 사용하여 EPC를 통해 eNB와 연결된 상태 (송출 중) - **이미 구현됨**
- **USRP B**: 이 스크립트를 사용하여 USRP A에 연결된 eNB에 연결 요청 flooding을 수행

## 사전 요구사항

### macOS에서 설치

1. **Homebrew 설치** (아직 없는 경우)
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. **의존성 설치**
   ```bash
   brew install cmake boost fftw libconfig
   ```

3. **UHD (USRP Hardware Driver) 설치**
   ```bash
   brew install uhd
   ```

4. **srsRAN 설치**
   ```bash
   git clone https://github.com/srsran/srsRAN.git
   cd srsRAN
   mkdir build
   cd build
   cmake ..
   make
   sudo make install
   ```
   
   **참고**: macOS에서 컴파일 시 일부 의존성 문제가 발생할 수 있습니다. 필요시 `brew install`로 추가 패키지를 설치하세요.

5. **Python 3.6 이상** (macOS에는 기본적으로 포함되어 있음)
   ```bash
   python3 --version  # 확인
   ```

### Linux에서 설치

1. **srsRAN 설치**
   ```bash
   git clone https://github.com/srsran/srsRAN.git
   cd srsRAN
   mkdir build
   cd build
   cmake ..
   make
   sudo make install
   ```

2. **USRP 드라이버 (UHD) 설치**
   ```bash
   # Ettus Research 공식 문서 참조
   # 또는 srsRAN 설치 시 자동으로 포함될 수 있음
   ```

3. **Python 3.6 이상**

## 사용법

### 기본 사용법

```bash
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL"
```

### 타겟 지정 방법

**MCC/MNC 또는 주파수 중 하나만 지정해도 됩니다:**

**방법 1: MCC/MNC로 지정 (주파수는 자동 스캔)**
핸드폰에 "123456"으로 표시되는 eNB (MCC=123, MNC=456)를 공격:

```bash
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL" --mcc 123 --mnc 456
```

**방법 2: 주파수로 지정 (해당 주파수의 모든 eNB 공격)**
특정 주파수(EARFCN)를 알고 있는 경우:

```bash
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL" --earfcn 3400
```

**방법 3: 둘 다 지정 (가장 정확)**
MCC/MNC와 주파수를 모두 알고 있는 경우:

```bash
python3 lte_flooding.py --usrp-args "serial=YOUR_USRP_SERIAL" --mcc 123 --mnc 456 --earfcn 3400
```

**참고**: 
- MCC/MNC만 지정하면 모든 주파수를 스캔하여 해당 PLMN을 찾습니다 (느릴 수 있음)
- 주파수만 지정하면 해당 주파수의 모든 eNB에 연결을 시도합니다
- 둘 다 지정하면 가장 빠르고 정확합니다

### 옵션

- `--usrp-args`: USRP 장치 인자
  - 예: `serial=30AD123` (시리얼 번호로 지정)
  - 예: `type=b200` (장치 타입으로 지정)
  - 예: `addr=192.168.10.2` (네트워크 주소로 지정)

- `--instances`: 동시에 실행할 srsUE 인스턴스 수 (기본값: 10)
  ```bash
  python3 lte_flooding.py --usrp-args "serial=30AD123" --instances 20
  ```

- `--interval`: 각 연결 시도 사이의 간격(초) (기본값: 0.1)
  ```bash
  python3 lte_flooding.py --usrp-args "serial=30AD123" --interval 0.05
  ```

- `--mcc`: Mobile Country Code (예: 123)
- `--mnc`: Mobile Network Code (예: 456)
  - MCC/MNC를 지정하면 해당 PLMN을 가진 eNB를 찾습니다.
  - 주파수를 모르는 경우 MCC/MNC만 지정해도 됩니다 (모든 주파수 스캔).
  - 핸드폰에 표시되는 번호는 `MCC` + `MNC`입니다 (예: MCC=123, MNC=456 → "123456")

- `--earfcn`: 주파수 채널 번호
  - 특정 주파수를 알고 있는 경우 주파수만 지정해도 됩니다.
  - MCC/MNC를 모르는 경우 주파수만 지정해도 해당 주파수의 모든 eNB에 공격 가능.
  - 기본값: 3400 (지정하지 않으면)

### USRP 장치 확인

USRP 장치를 확인하려면:
```bash
uhd_find_devices
```

또는 srsRAN의 도구 사용:
```bash
srsran_usrp_probe
```

## 작동 원리

1. 스크립트는 지정된 수의 srsUE 인스턴스를 동시에 실행합니다.
2. 각 인스턴스는 고유한 IMSI와 IMEI를 가진 UE로 동작합니다.
3. 각 UE는 지정된 주파수(EARFCN)에서 eNB를 스캔합니다.
4. MCC/MNC가 지정된 경우, 해당 PLMN을 가진 eNB만 선택합니다.
5. 각 UE는 선택된 eNB에 연결을 시도합니다.
6. 연결이 실패하거나 종료되면, 설정된 간격 후 재시도합니다.
7. 이 과정이 반복되어 eNB에 flooding 효과를 만듭니다.

## 주의사항

⚠️ **법적 고지**: 이 도구는 교육 및 합법적인 보안 테스트 목적으로만 사용해야 합니다. 무단으로 무선 네트워크에 공격을 수행하는 것은 불법입니다.

- 테스트 환경에서만 사용하세요.
- 적절한 라이선스를 가진 주파수 대역에서만 사용하세요.
- 네트워크 운영자의 명시적 허가 없이 실제 네트워크에 사용하지 마세요.

## 문제 해결

### USRP 장치를 찾을 수 없음
- `uhd_find_devices`로 장치가 인식되는지 확인
- USB 케이블 연결 확인
- **macOS**: USB 장치 권한 확인 (시스템 설정 > 보안 및 개인 정보 보호)
- **Linux**: 권한 문제: `sudo`로 실행하거나 udev 규칙 설정

### srsUE를 찾을 수 없음
- srsRAN이 올바르게 설치되었는지 확인: `which srsue`
- PATH에 srsRAN 바이너리가 포함되어 있는지 확인

### 연결 실패
- eNB가 실행 중인지 확인
- 주파수 설정이 올바른지 확인 (dl_earfcn)
- USRP A와 USRP B가 같은 주파수 대역을 사용하는지 확인

## 로그

각 UE 인스턴스의 로그는 `srsue_<instance_id>.log` 파일에 저장됩니다.

## 라이선스

이 프로젝트는 교육 및 연구 목적으로 제공됩니다.

