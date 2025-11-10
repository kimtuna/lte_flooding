# macOS에서 srsRAN 설치 가이드

## 사전 요구사항

### 1. Homebrew 설치 확인
```bash
brew --version
```
Homebrew가 없으면:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2. 필수 의존성 설치
```bash
brew install cmake boost fftw libconfig sox mbedtls libusrsctp
```

**중요**: 
- MbedTLS는 필수입니다
- SCTP는 테스트 코드에 필요할 수 있습니다 (libusrsctp 설치)

### 3. UHD (USRP Hardware Driver) 설치
```bash
brew install uhd
```

### 4. 추가 의존성 (필요시)
```bash
brew install pkg-config
```

## srsRAN 설치

### 1. 소스 코드 다운로드
```bash
cd ~
git clone https://github.com/srsran/srsRAN.git
cd srsRAN
```

### 2. 빌드 디렉토리 생성 및 빌드
```bash
mkdir build
cd build
cmake -DCMAKE_PREFIX_PATH=$(brew --prefix) ..
make -j$(sysctl -n hw.ncpu)
```

**참고**: `-DCMAKE_PREFIX_PATH=$(brew --prefix)`를 추가하면 Homebrew로 설치한 라이브러리를 자동으로 찾습니다.

**참고**: `-j$(sysctl -n hw.ncpu)`는 CPU 코어 수만큼 병렬 빌드하여 속도를 높입니다.

### 3. 설치
```bash
sudo make install
```

### 4. 라이브러리 경로 설정 (필요시)
```bash
# ~/.zshrc 또는 ~/.bash_profile에 추가
export DYLD_LIBRARY_PATH=/usr/local/lib:$DYLD_LIBRARY_PATH
export PATH=/usr/local/bin:$PATH
```

## 설치 확인

```bash
# srsUE 확인
which srsue
srsue --version

# srsENB 확인
which srsenb
srsenb --version

# USRP 장치 확인
uhd_find_devices
```

## 문제 해결

### 1. 컴파일 오류 발생 시
- Xcode Command Line Tools 설치 확인:
  ```bash
  xcode-select --install
  ```

### 2. 라이브러리를 찾을 수 없는 경우 (MbedTLS 등)
```bash
# MbedTLS 설치 확인
brew list mbedtls

# Homebrew로 설치한 라이브러리 경로 확인
brew --prefix
# 예: /opt/homebrew

# CMake에 경로 지정하여 다시 빌드
cd ~/srsRAN/build
rm -rf *  # 기존 빌드 파일 삭제
cmake -DCMAKE_PREFIX_PATH=$(brew --prefix) ..
make -j$(sysctl -n hw.ncpu)
```

**MbedTLS 오류 해결**:
```bash
# MbedTLS 설치
brew install mbedtls

# 빌드 디렉토리 정리 후 재빌드
cd ~/srsRAN/build
rm -rf *
cmake -DCMAKE_PREFIX_PATH=$(brew --prefix) ..
make -j8
```

**SCTP 오류 해결**:
```bash
# 방법 1: libusrsctp 설치 (권장)
brew install libusrsctp

# 빌드 디렉토리 정리 후 재빌드
cd ~/srsRAN/build
rm -rf *
cmake -DCMAKE_PREFIX_PATH=$(brew --prefix) ..
make -j8

# 방법 2: SCTP를 선택적으로 비활성화 (테스트 코드만 건너뛰기)
cd ~/srsRAN/build
rm -rf *
cmake -DCMAKE_PREFIX_PATH=$(brew --prefix) -DENABLE_SCTP=OFF ..
make -j8
```

**참고**: SCTP는 주로 테스트 코드에서 사용되므로, 실제 사용에는 필수가 아닐 수 있습니다.

**Boost system 컴포넌트 오류 해결**:
```bash
# Boost system 라이브러리 확인
ls -la /opt/homebrew/lib/libboost_system*

# Boost가 설치되어 있지만 system 컴포넌트를 찾지 못하는 경우
# CMake에 Boost 경로를 명시적으로 지정
cd ~/srsRAN/build
rm -rf *
cmake -DCMAKE_PREFIX_PATH=$(brew --prefix) \
      -DBoost_INCLUDE_DIR=$(brew --prefix boost)/include \
      -DBoost_LIBRARY_DIR=$(brew --prefix boost)/lib \
      -DBoost_NO_BOOST_CMAKE=ON \
      ..
make -j8
```

**또는 Boost 컴포넌트를 명시적으로 지정**:
```bash
cd ~/srsRAN/build
rm -rf *
cmake -DCMAKE_PREFIX_PATH=$(brew --prefix) \
      -DBOOST_ROOT=$(brew --prefix boost) \
      -DBoost_INCLUDE_DIR=$(brew --prefix boost)/include \
      -DBoost_LIBRARY_DIR=$(brew --prefix boost)/lib \
      ..
make -j8
```

### 3. USRP 장치를 찾을 수 없는 경우
- USB 권한 확인 (시스템 설정 > 보안 및 개인 정보 보호)
- UHD 이미지 다운로드:
  ```bash
  uhd_images_downloader
  ```

### 4. 권한 문제
일부 경우 `sudo`가 필요할 수 있습니다:
```bash
sudo make install
```

## 참고사항

- macOS에서 srsRAN은 공식적으로 완전히 지원되지 않을 수 있습니다
- 일부 기능이 제한될 수 있습니다
- 성능은 Linux보다 낮을 수 있습니다
- 문제가 발생하면 srsRAN GitHub 이슈를 확인하세요: https://github.com/srsran/srsRAN/issues

## 대안: Docker 사용

macOS에서 더 안정적인 실행을 원한다면 Docker를 사용할 수 있습니다:
```bash
# srsRAN Docker 이미지 사용 (있는 경우)
docker pull srsran/srsran
```

