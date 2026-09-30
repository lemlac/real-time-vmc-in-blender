bl_info = {
    "name": "OpenVTube Core VMC Engine",
    "author": "Open Source Community",
    "version": (1, 1, 0),
    "blender": (4, 2, 0),
    "category": "Animation",
    "description": "Pure Python Open-Source VMC/OSC Binary Receiver and Parser",
}

import blender_utils
import struct
import socket
import threading
import queue
import bpy

# Global network storage
vmc_data_queue = queue.Queue()
network_receiver_thread = None
is_listening = False

def decode_osc_string(data, offset):
    """Reads a null-terminated string padded to 4-byte boundaries."""
    start = offset
    while offset < len(data) and data[offset] != 0:
        offset += 1
    string_val = data[start:offset].decode('utf-8', errors='ignore')
    offset = (offset + 4) & ~3  # Round up to next 4-byte boundary
    return string_val, offset

def parse_vmc_packet(binary_data):
    """
    Pure Python binary parser for VMC OSC data streams.
    Decodes network byte packets into structured bone and expression dictionaries.
    """
    try:
        if len(binary_data) < 4:
            return None
            
        # 1. Parse OSC Address Pattern
        address, offset = decode_osc_string(binary_data, 0)
        
        # We target core tracking routes
        if address not in ["/VMC/Ext/Bone", "/VMC/Ext/Blend/Val"]:
            return None
            
        # 2. Parse Type Tags (e.g., ",sffffffff")
        type_tags, offset = decode_osc_string(binary_data, offset)
        if not type_tags.startswith(','):
            return None
            
        args = []
        tags = type_tags[1:]
        
        # 3. Unpack arguments based on binary type markers
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
    """Listens natively on UDP network interfaces without locking Blender's UI thread."""
    global is_listening
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5)
    
    try:
        sock.bind((host, port))
        print(f"[OpenVTube] Native OSC server online: {host}:{port}")
    except Exception as err:
        print(f"[OpenVTube Network Error]: Can't bind port. {err}")
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
        except Exception as e:
            print(f"[OpenVTube Worker Exception]: {e}")
            
    sock.close()
    print("[OpenVTube] Network socket offline.")

def process_queue_to_blender():
    """Timer loop draining queues safely inside Blender's thread loops."""
    global is_listening
    
    # Process up to 150 items per tick to prevent UI lag while maintaining low latency
    for _ in range(150):
        if vmc_data_queue.empty():
            break
            
        try:
            item = vmc_data_queue.get_nowait()
            address = item["address"]
            args = item["args"]
            
            # Route A: Bone Tracking transformations
            if address == "/VMC/Ext/Bone" and len(args) >= 8:
                bone_name, px, py, pz, rx, ry, rz, rw = args
                
                # Check active context scene selections
                obj = bpy.context.view_layer.objects.active
                if obj and obj.type == 'ARMATURE' and obj.mode == 'POSE':
                    if bone_name in obj.pose.bones:
                        bone = obj.pose.bones[bone_name]
                        # VMC passes tracking data via standard Quaternions
                        bone.rotation_mode = 'QUATERNION'
                        # Handle raw assignment or map coordinate transformations
                        bone.rotation_quaternion = (rw, rx, ry, rz)
                        bone.location = (px, py, pz)
                        
            # Route B: Expression / Facial Tracking Shape Keys
            elif address == "/VMC/Ext/Blend/Val" and len(args) >= 2:
                shape_name, raw_value = args
                
                obj = bpy.context.view_layer.objects.active
                if obj and obj.type == 'MESH' and obj.data.shape_keys:
                    key_blocks = obj.data.shape_keys.key_blocks
                    if shape_name in key_blocks:
                        # Linear Interpolation smoothing (Lerp: 70% current, 30% new packet target value)
                        current_val = key_blocks[shape_name].value
                        smoothed_val = current_val + 0.3 * (raw_value - current_val)
                        key_blocks[shape_name].value = smoothed_val
                        
        except queue.Empty:
            break
            
    return 0.008 if is_listening else None  # Target an ~120Hz thread refresh rate

# --- User Interface & Operator Registrations ---

class OP_OPENVTUBE_START(bpy.types.Operator):
    bl_idname = "openvtube.start_receiver"
    bl_label = "Start Receiver"
    
    def execute(self, context):
        global is_listening, network_receiver_thread
        if is_listening:
            return {'FINISHED'}
            
        props = context.scene.openvtube_props
        is_listening = True
        
        network_receiver_thread = threading.Thread(
            target=background_network_worker, 
            args=(props.network_host, props.network_port),
            daemon=True
        )
        network_receiver_thread.start()
        
        # Register core loop within Blender Application Scheduler
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
    bl_label = "VMC Binary Receiver Settings"
    
    def draw(self, context):
        layout = self.layout
        props = context.scene.openvtube_props
        
        col = layout.column(align=True)
        col.prop(props, "network_host", text="IP Address")
        col.prop(props, "network_port", text="UDP Port")
        
        row = layout.row(align=True)
        row.operator("openvtube.start_receiver", icon='PLAY')
        row.operator("openvtube.stop_receiver", icon='PAUSE')

class OpenVTubeProperties(bpy.types.PropertyGroup):
    network_host: bpy.props.StringProperty(default="127.0.0.1")
    network_port: bpy.props.IntProperty(default=39539, min=1024, max=65535)

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

if __name__ == "__main__":
    register()
