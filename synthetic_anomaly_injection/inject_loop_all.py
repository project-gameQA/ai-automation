#언리얼 엔진에서 스크립트로 실행

import unreal

def generate_missing_material_dataset(output_dir):
    all_actors = unreal.EditorLevelLibrary.get_all_level_actors()
    mesh_actors = [a for a in all_actors if a.get_component_by_class(unreal.StaticMeshComponent)]
    results = []
    
    for idx, actor in enumerate(mesh_actors):
        results_meta = inject_missing_material(actor, output_dir, idx)
        results.extend(results_meta)
    
    return results

