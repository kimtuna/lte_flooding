#!/bin/bash
# LTE Flooding 사용 예제 스크립트

echo "=== LTE Flooding 사용 예제 ==="
echo ""

# USRP 장치 확인
echo "1. USRP 장치 확인 중..."
uhd_find_devices

echo ""
echo "2. 사용할 USRP 장치의 시리얼 번호나 주소를 확인하세요."
echo ""

# 기본 사용 예제
echo "3. 기본 사용법 (10개 인스턴스, 0.1초 간격):"
echo "   python3 lte_flooding.py --usrp-args \"serial=YOUR_SERIAL\""
echo ""

# 고강도 flooding 예제
echo "4. 고강도 flooding (20개 인스턴스, 0.05초 간격):"
echo "   python3 lte_flooding.py --usrp-args \"serial=YOUR_SERIAL\" --instances 20 --interval 0.05"
echo ""

# 네트워크 USRP 사용 예제
echo "5. 네트워크 USRP 사용:"
echo "   python3 lte_flooding.py --usrp-args \"addr=192.168.10.2\""
echo ""

echo "주의: Ctrl+C로 중지할 수 있습니다."

