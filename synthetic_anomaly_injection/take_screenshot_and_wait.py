#언리얼 엔진에서 스크립트로 실행

import time
import unreal 
import os

def take_screenshot_and_wait(width, height, path, timeout=5.0):
    #혹시 전단게에 남아있을 만한 것 삭제
    if os.path.exists(path):
        os.remove(path)

    unreal.AutomationLibrary.take_high_res_screenshot(width,height,path)
    start = time.time()

    #파일이 쓰여지기 전에 크기
    last_size = -1

    while time.time() - start < timeout:
        
        #0.1초 단위로 크기를 업데이트 하면서 변화가 없으면 True 반환
        if os.path.exists(path):
            size = os.path.getsize(path)

            #크기가 변화가 없으면
            if size > 0 and size == last_size:
                return True
            last_size = size

        time.sleep(0.1)
    
    return False