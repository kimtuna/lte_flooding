# Boost System 오류 해결 방법

## 문제
srsRAN 빌드 시 Boost system 컴포넌트를 찾지 못하는 오류가 발생합니다.

## 원인
최신 Boost 버전(1.89.0)에서는 system이 헤더 전용이거나 별도 라이브러리가 필요하지 않을 수 있습니다.

## 해결 방법

### 방법 1: srsRAN의 CMakeLists.txt 수정 (권장)

1. srsRAN 소스 디렉토리로 이동:
```bash
cd ~/srsRAN
```

2. CMakeLists.txt에서 Boost system 요구사항 확인:
```bash
grep -n "boost.*system" CMakeLists.txt
```

3. Boost system을 선택적으로 만들기:
   - CMakeLists.txt를 열어서 Boost system 요구사항을 찾습니다
   - `find_package(Boost REQUIRED COMPONENTS system ...)` 같은 부분을 찾습니다
   - `REQUIRED`를 제거하거나 system을 선택적으로 만듭니다

### 방법 2: CMake에서 Boost system 무시

```bash
cd ~/srsRAN/build
rm -rf *
cmake -DCMAKE_PREFIX_PATH=$(brew --prefix) \
      -DBOOST_ROOT=$(brew --prefix boost) \
      -DBoost_INCLUDE_DIR=$(brew --prefix boost)/include \
      -DBoost_LIBRARY_DIR=$(brew --prefix boost)/lib \
      -DBoost_NO_BOOST_CMAKE=ON \
      -DBoost_SYSTEM_FOUND=TRUE \
      ..
```

### 방법 3: 더 낮은 버전의 Boost 사용

```bash
# 특정 버전의 Boost 설치 (예: 1.82)
brew install boost@1.82
brew link boost@1.82 --force

# 빌드
cd ~/srsRAN/build
rm -rf *
cmake -DCMAKE_PREFIX_PATH=$(brew --prefix) ..
```

### 방법 4: srsRAN GitHub 이슈 확인

srsRAN의 GitHub에서 macOS 관련 이슈를 확인:
- https://github.com/srsran/srsRAN/issues

## 빠른 해결 (시도해볼 명령어)

```bash
cd ~/srsRAN/build
rm -rf *
cmake -DCMAKE_PREFIX_PATH=$(brew --prefix) \
      -DBOOST_ROOT=$(brew --prefix boost) \
      -DBoost_INCLUDE_DIR=$(brew --prefix boost)/include \
      -DBoost_LIBRARY_DIR=$(brew --prefix boost)/lib \
      -DBoost_NO_BOOST_CMAKE=ON \
      -DBoost_SYSTEM_FOUND=TRUE \
      -DBoost_SYSTEM_LIBRARY=/opt/homebrew/lib/libboost_filesystem.dylib \
      ..
```

**참고**: 위 명령어는 Boost system을 filesystem으로 대체하려고 시도합니다. 실제로는 srsRAN의 CMakeLists.txt를 수정하는 것이 가장 확실한 방법입니다.

