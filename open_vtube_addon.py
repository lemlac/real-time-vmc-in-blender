bl_info = {
    "name": "OpenVTube Core VMC Engine (With Bone Mapping)",
    "author": "Open Source Community",
    "version": (1, 2, 0),
    "blender": (4, 2, 0),
    "category": "Animation",
    "description": "Pure Python Open-Source VMC/OSC Receiver with Automated Character Bone Mapping",
}

import struct
import socket
import threading
import queue
import bpy
import mathutils

# Global network storage
vmc_data_queue = queue.Queue()
network_receiver_thread = None
is_listening = False

# ==============================================================================
# BONE REMAPPING TRANSLATION TABLES
# ==============================================================================
# Maps incoming standard Unity Humanoid/VRM bone tags directly to Blender Rig structures.
BONE_MAPPING_PRESETS = {
    "Mixamo": {
        "Hips": "Hips",
        "Spine": "Spine",
        "Chest": "Spine1",
        "UpperChest": "Spine2",
        "Neck": "Neck",
        "Head": "Head",
        "LeftUpperArm": "LeftArm",
        "LeftLowerArm": "LeftForeArm",
        "LeftHand": "LeftHand",
        "RightUpperArm": "RightArm",
        "RightLowerArm": "RightForeArm",
        "RightHand": "RightHand",
        "LeftUpperLeg": "LeftUpLeg",
        "LeftLowerLeg": "LeftLeg",
        "LeftFoot": "LeftFoot",
        "RightUpperLeg": "RightUpLeg",
        "RightLowerLeg": "RightLeg",
        "RightFoot": "RightFoot"
    },
    "Rigify": {
        "Hips": "tweak_spine",
        "Spine": "spine",
        "Chest": "spine.001",
        "UpperChest": "spine.002",
        "Neck": "neck",
        "Head": "head",
        "LeftUpperArm": "upper_arm.L",
        "LeftLowerArm": "forearm.L",
        "LeftHand": "hand.L",
        "RightUpperArm": "upper_arm.R",
        "RightLowerArm": "forearm.R",
        "RightHand": "hand.R",
        "LeftUpperLeg": "thigh.L",
        "LeftLowerLeg": "shin.L",
        "LeftFoot": "foot.L",
        "RightUpperLeg": "thigh.R",
        "RightLowerLeg": "shin.R",
        "RightFoot": "foot.R"
    }
}

# ==============================================================================
# NATIVE BINARY PARSER LOGIC
# ==============================================================================
def decode_osc_string(data, offset):
    start = offset
    while offset < len(data) and data[offset] != 0:
        offset += 1
    string_val = data[start:offset].decode('utf-8', errors='ignore')
    offset = (offset + 4) & ~3  # Round to 4-byte boundaries
    return string_val, offset

def parse_vmc_packet(binary_data):
    try:
        if len(binary_data) < 4:
            return None
        address, offset = decode_osc_string(binary_data, 0)
        if address not in ["/VMC/Ext/Bone", "/VMC/Ext/Blend/Val"]:
            return None
            
        type_tags, offset = decode_osc_string(binary_data, offset)
        if not type_tags.startswith(','):
            return None
            
        args = []
        tags = type_tags[1:]
        
        for tag in tags:
            if tag == 's':
                string_val, offset = decode_osc_string(binary_data, offset)
                args.append(string_val)
            elif tag == 'f':
                if offset + 4 <= len(binary_data):
                    float_val = struct.unpack('>f', binary_data[offset:offset+4])[0]
                    args.append(float_val)
                    offset += 4
            elif tag == 'i':
                if offset + 4 <= len(binary_data):
                    int_val = struct.unpack('>i', binary_data[offset:offset+4])[0]
                    args.append(int_val)
                    offset += 4
                    
        return {"address": address, "args": args}
    except Exception as e:
        print(f"[OpenVTube Parser Error]: {e}")
        return None

def background_network_worker(host, port):
    global is_listening
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    try:
        sock.bind((host, port))
    except Exception as err:
        print(f"[OpenVTube Network Error]: {err}")
        is_listening = False
        return

    while is_listening:
        try:
            packet, _ = sock.recvfrom(2048)
            parsed_msg = parse_vmc_packet(packet)
            if parsed_msg:
                vmc_data_queue.put(parsed_msg)
        except socket.timeout:
            continue
    sock.close()

# ==============================================================================
# MAIN EXECUTION ENGINE WITH TRANSFORMATIONS
# ==============================================================================
def process_queue_to_blender():
    global is_listening
    props = bpy.context.scene.openvtube_props
    active_preset = BONE_MAPPING_PRESETS.get(props.rig_preset, {})

    for _ in range(150):
        if vmc_data_queue.empty():
            break
        try:
            item = vmc_data_queue.get_nowait()
            address = item["address"]
            args = item["args"]
            
            if address == "/VMC/Ext/Bone" and len(args) >= 8:
                vrm_bone_name, px, py, pz, rx, ry, rz, rw = args
                
                # Check mapping dictionary to find corresponding target name
                blender_bone_name = active_preset.get(vrm_bone_name, vrm_bone_name)
                
                obj = bpy.context.view_layer.objects.active
                if obj and obj.type == 'ARMATURE' and obj.mode == 'POSE':
                    if blender_bone_name in obj.pose.bones:
                        bone = obj.pose.bones[blender_bone_name]
                        bone.rotation_mode = 'QUATERNION'
                        
                        # Unity (Left-Handed) to Blender (Right-Handed) space translation
                        # Unity: X-Right, Y-Up, Z-Forward -> Blender: X-Right, Y-Back, Z-Up
                        raw_quat = mathutils.Quaternion((rw, rx, ry, rz))
                        
                        # Coordinate transformation formula
                        converted_quat = mathutils.Quaternion((raw_quat.w, -raw_quat.x, -raw_quat.z, -raw_quat.y))
                        
                        # Apply transformation to the local bone coordinate system
                        bone.rotation_quaternion = converted_quat
                        
                        # Handle Root Hip spatial transitions
                        if vrm_bone_name == "Hips":
                            bone.location = mathutils.Vector((px, -pz, py))
                            
            elif address == "/VMC/Ext/Blend/Val" and len(args) >= 2:
                shape_name, raw_value = args
                obj = bpy.context.view_layer.objects.active
                if obj and obj.type == 'MESH' and obj.data.shape_keys:
                    key_blocks = obj.data.shape_keys.key_blocks
                    if shape_name in key_blocks:
                        current_val = key_blocks[shape_name].value
                        key_blocks[shape_name].value = current_val + 0.3 * (raw_value - current_val)
        except queue.Empty:
            break
            
    return 0.008 if is_listening else None

# ==============================================================================
# UI BLENDER REGISTRATIONS
# ==============================================================================
class OpenVTubeProperties(bpy.types.PropertyGroup):
    network_host: bpy.props.StringProperty(default="127.0.0.1")
    network_port: bpy.props.IntProperty(default=39539, min=1024, max=65535)
    rig_preset: bpy.props.EnumProperty(
        name="Rig Profile",
        description="Select mapping dictionary structure for target armature",
        items=[
            ('Mixamo', "Mixamo (Adobe)", "Standard Mixamo Bone Nomenclature"),
            ('Rigify', "Rigify (Blender)", "Default Blender Meta-Rig Structure"),
            ('Default', "Identity Map", "Directly uses raw tracked bone strings")
        ],
        default='Mixamo'
    )

class OP_OPENVTUBE_START(bpy.types.Operator):
    bl_idname = "openvtube.start_receiver"
    bl_label = "Start Receiver"
    def execute(self, context):
        global is_listening, network_receiver_thread
        if is_listening: return {'FINISHED'}
        props = context.scene.openvtube_props
        is_listening = True
        network_receiver_thread = threading.Thread(
            target=background_network_worker, 
            args=(props.network_host, props.network_port),
            daemon=True
        )
        network_receiver_thread.start()
        bpy.app.timers.register(process_queue_to_blender)
        self.report({'INFO'}, "OpenVTube Engine Started")
        return {'FINISHED'}

class OP_OPENVTUBE_STOP(bpy.types.Operator):
    bl_idname = "openvtube.stop_receiver"
    bl_label = "Stop Receiver"
    def execute(self, context):
        global is_listening
        is_listening = False
        self.report({'INFO'}, "OpenVTube Engine Stopped")
        return {'FINISHED'}

class VIEW3D_PT_OPENVTUBE_PANEL(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'OpenVTube'
    bl_label = "VMC Automated Character Mapping"
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.openvtube_props
        
        box = layout.box()
        box.label(text="Network Configuration", icon='NETWORK_DRIVE')
        box.prop(props, "network_host", text="IP")
        box.prop(props, "network_port", text="Port")
        
        box = layout.box()
        box.label(text="Profile Mapping Matrix", icon='ARMATURE_DATA')
        box.prop(props, "rig_preset", text="Target Profile")
        
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("openvtube.start_receiver", icon='PLAY')
        row.operator("openvtube.stop_receiver", icon='PAUSE')

classes = (
    OpenVTubeProperties,
    OP_OPENVTUBE_START,
    OP_OPENVTUBE_STOP,
    VIEW3D_PT_OPENVTUBE_PANEL
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.openvtube_props = bpy.props.PointerProperty(type=OpenVTubeProperties)

def unregister():
    global is_listening
    is_listening = False
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.openvtube_props

if name == "main":
    register()
