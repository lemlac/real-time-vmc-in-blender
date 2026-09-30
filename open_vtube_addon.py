bl_info = {
    "name": "OpenVTube Boilerplate",
    "author": "Open Source Community",
    "version": (1, 0),
    "blender": (4, 0, 0),
    "location": "View3D > Sidebar > OpenVTube",
    "description": "An open-source boilerplate for real-time VTubing data ingestion via UDP/VMC protocol.",
    "category": "Animation",
}

import bpy
import socket
import threading
import queue
import json

# Global variables for thread management
_network_thread = None
_data_queue = queue.Queue()
_stop_signal = threading.Event()

class OPENVTUBE_PT_sidebar(bpy.types.Panel):
    """Creates a Panel in the 3D Viewport Sidebar"""
    bl_label = "OpenVTube Controller"
    bl_idname = "OPENVTUBE_PT_sidebar"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'OpenVTube'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        layout.label(text="Network Settings:")
        layout.prop(scene, "openvtube_port", text="Port")
        
        layout.separator()
        
        if not context.scene.openvtube_is_running:
            layout.operator("openvtube.start_stream", text="Start Receiver", icon='PLAY')
        else:
            layout.operator("openvtube.stop_stream", text="Stop Receiver", icon='PAUSE')

def network_listener(port, data_queue, stop_signal):
    """Background thread worker that listens for incoming UDP packets safely."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.5) # Prevent eternal blocking on join
    try:
        sock.bind(("127.0.0.1", port))
    except Exception as e:
        print(f"[OpenVTube] Failed to bind port {port}: {e}")
        return

    print(f"[OpenVTube] UDP Server listening on port {port}...")

    while not stop_signal.is_set():
        try:
            data, addr = sock.recvfrom(1024) # Standard buffer size
            # Packet parsing payload structure simulation
            # VMC/OSC protocols use binary format, but for this boilerplate 
            # we demonstrate handling string/json translation patterns safely.
            payload = data.decode('utf-8', errors='ignore')
            data_queue.put(payload)
        except socket.timeout:
            continue
        except Exception as e:
            print(f"[OpenVTube] Error receiving data: {e}")
            break

    sock.close()
    print("[OpenVTube] UDP Server stopped successfully.")

def openvtube_timer_loop():
    """Main thread execution frame timer loop triggered safely by Blender."""
    if not bpy.context.scene.openvtube_is_running:
        return None # Unregisters the timer dynamically

    # Process all pending network packets currently in queue
    while not _data_queue.empty():
        try:
            raw_packet = _data_queue.get_nowait()
            
            # Implementation Pattern: Parse your tracking protocol structure here
            # For demonstration, we assume incoming packets might contain a basic mock instruction
            # e.g., '{"target": "Head", "rotation": [0, 0, 0, 1]}' or a raw tracking flag
            
            # Safely check active armature constraints
            obj = bpy.context.active_object
            if obj and obj.type == 'ARMATURE' and obj.mode == 'POSE':
                # Example tracking execution block targeting a bone named "Head"
                if "Head" in obj.pose.bones:
                    bone = obj.pose.bones["Head"]
                    # Apply mock calculation adjustments or simple lerp filter here
                    pass
                    
            # Tag the viewport region for redraw to force smooth real-time graphics update
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        area.tag_redraw()
                        
        except queue.Empty:
            break
        except Exception as e:
            print(f"[OpenVTube] Error handling pipeline data execution frame loop: {e}")

    return 0.01 # Keep executing frame ticks roughly every 10ms (~100Hz tracking evaluation)

class OPENVTUBE_OT_start_stream(bpy.types.Operator):
    """Start background network listener threads"""
    bl_idname = "openvtube.start_stream"
    bl_label = "Start OpenVTube"
    
    def execute(self, context):
        global _network_thread, _stop_signal
        
        context.scene.openvtube_is_running = True
        _stop_signal.clear()
        
        # Clear out any stale junk elements from previous runs
        while not _data_queue.empty():
            try: _data_queue.get_nowait()
            except queue.Empty: break

        # Initialize network workers
        port = context.scene.openvtube_port
        _network_thread = threading.Thread(
            target=network_listener, 
            args=(port, _data_queue, _stop_signal),
            daemon=True
        )
        _network_thread.start()
        
        # Schedule the Blender runtime main thread execution worker
        bpy.app.timers.register(openvtube_timer_loop)
        self.report({'INFO'}, f"OpenVTube tracking receiver active on port {port}")
        return {'FINISHED'}

class OPENVTUBE_OT_stop_stream(bpy.types.Operator):
    """Stop background network listener threads"""
    bl_idname = "openvtube.stop_stream"
    bl_label = "Stop OpenVTube"
    
    def execute(self, context):
        global _network_thread, _stop_signal
        
        context.scene.openvtube_is_running = False
        _stop_signal.set()
        
        if _network_thread:
            _network_thread.join(timeout=1.0)
            _network_thread = None
            
        self.report({'INFO'}, "OpenVTube tracking receiver stopped.")
        return {'FINISHED'}

def register():
    bpy.utils.register_class(OPENVTUBE_PT_sidebar)
    bpy.utils.register_class(OPENVTUBE_OT_start_stream)
    bpy.utils.register_class(OPENVTUBE_OT_stop_stream)
    
    bpy.types.Scene.openvtube_port = bpy.props.IntProperty(name="Port", default=39539, min=1024, max=65535)
    bpy.types.Scene.openvtube_is_running = bpy.props.BoolProperty(name="Running Status", default=False)

def unregister():
    # Enforce cleanup if add-on is uninstalled while executing
    global _stop_signal
    _stop_signal.set()
    
    bpy.utils.unregister_class(OPENVTUBE_PT_sidebar)
    bpy.utils.unregister_class(OPENVTUBE_OT_start_stream)
    bpy.utils.unregister_class(OPENVTUBE_OT_stop_stream)
    
    del bpy.types.Scene.openvtube_port
    del bpy.types.Scene.openvtube_is_running

if __name__ == "__main__":
    register()
