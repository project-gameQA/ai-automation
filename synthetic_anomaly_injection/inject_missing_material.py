#언리얼 엔진에서 스크립트로 실행

import unreal
import json
import os

def inject_missing_material(actor, output_dir, actor_index):
    

    #StaticMeshComponent 클래스의 컴포넌트 구하기
    mesh_comp = actor.get_component_by_class(unreal.StaticMeshComponent)
    if not mesh_comp:
        return []

    #각 컴포넌트들은 property를 가짐. property를 이름으로 읽기.
    #get_editor_property, set_editor_property...
    original_materials = mesh_comp.get_editor_property("override_materials")

    #material slot 갯수. 슬롯대로 차례대로 돌아가면서 missing value를 집어넣기 위해서 몇 개있는지 카운트.
    num_slots = mesh_comp.get_num_materials()

    results_metadata = []
    for idx in range(num_slots):
        
        sample_id = f"missing_material_{actor_index}_slot_{idx}"
        mesh_comp.set_material(idx,None)
        
        #스크린샷 데이터 만들기
        screenshot_path = os.path.join(output_dir, f"{sample_id}.png")
        
        # unreal.AutomationLibrary.take_high_res_screenshot(
        #     1280,720,screenshot_path
        # )
        
        #스크린샷 도중 딜레이가 생겨서 반복문이 겹치거나 오류가 생길 수 있으므로
        success = take_screenshot_and_wait(1280, 720, screenshot_path)
        if not success:
            print(f"스크린샷 실패, {sample_id}")

        
        #메타데이터 만들기
        metadata = {
            "sample_id": sample_id,
            "bug_class": "missing_material",
            "actor_name": actor.get_actor_label(),
            "injected_slot": idx,
            "total_slots": num_slots,
            "image_path": screenshot_path,
        }

        with open(os.path.join(output_dir, f"{sample_id}.json"), "w") as f:
            json.dump(metadata, f)
        
        results_metadata.append(metadata)

        mesh_comp.set_editor_property("override_materials", original_materials)
    
    return results_metadata