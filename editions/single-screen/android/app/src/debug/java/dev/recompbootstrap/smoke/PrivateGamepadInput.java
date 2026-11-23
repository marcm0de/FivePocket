package dev.recompbootstrap.smoke;

import android.content.Context;
import android.hardware.input.InputManager;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;

/** One actual Android controller mapped to guest slot zero. No user sign-in. */
final class PrivateGamepadInput implements InputManager.InputDeviceListener {
    private static final String TAG="RecompNativeInput";
    private final InputManager manager;
    private final XboxGamepadMapper mapper=new XboxGamepadMapper();
    private final KeyboardPad keyboard=new KeyboardPad(); // Discord item 10: a Bluetooth/USB keyboard plays as the pad (KeyboardPad's layout)
    private final PhysicalMotionLog motionLog=new PhysicalMotionLog(); // stick-only play leaves a line too, at most one in 5 s
    // Android sends no joystick event while no value changes (a stick held against its end stop): the line of a stick
    // that is held still is written by this timer, at the time PhysicalMotionLog names.
    private final Handler motionTimer=new Handler(Looper.getMainLooper());
    private final Runnable heldCheck=this::heldCheck;
    private long heldCheckAt=Long.MIN_VALUE;
    private int deviceId=-1, rightAxisX=-1, rightAxisY=-1, leftAxis=-1, rightAxis=-1;
    // Discord D5 (2026-11-10): the right stick's pair is RightStickPicker's (static ranges first, then where the axes rest)
    private static final int[] PICK_AXES={MotionEvent.AXIS_Z,MotionEvent.AXIS_RZ,MotionEvent.AXIS_RX,MotionEvent.AXIS_RY};
    private RightStickPicker.Axis[] candidates=new RightStickPicker.Axis[4];
    private final RightStickPicker.Rest rest=new RightStickPicker.Rest();
    private int restDevice=-1, pair=RightStickPicker.PAIR_NONE;private boolean restDecided;
    private Runnable observer;
    // upd1-wave3 android-dualsense: Haptics.controller, told the controller the game is given (RumbleTarget); null: no game start
    private java.util.function.IntConsumer controllerListener;
    private String description="No controller", lastEvent="No input received";
    PrivateGamepadInput(Context context) {
        manager=(InputManager)context.getSystemService(Context.INPUT_SERVICE);
        if(manager!=null)manager.registerInputDeviceListener(this,motionTimer);
        QolTouch.attachPad(this::publish); // qol-touch (single-screen QoL edition): long presses on the game screen drive the map and the wheels
        QolTouch.attachDevices(()->use(deviceId)); // touch-controls: the on-screen pad is a controller (connected, full capabilities)
        refresh(true);
    }
    void observe(Runnable listener) {observer=listener;publish();}
    /** upd1-wave3 android-dualsense: listener hears the playing controller's device number now and on every change (below 0: none). */
    void onController(java.util.function.IntConsumer listener) {controllerListener=listener;if(listener!=null)listener.accept(deviceId);}
    String description() {return description;}
    String lastEvent() {return lastEvent;}
    int[] physicalState() {return keyboard.merge(new int[]{mapper.physicalButtons(),mapper.physicalLeftTrigger(),mapper.physicalRightTrigger(),
            mapper.leftX,mapper.leftY,mapper.rightX,mapper.rightY});}
    private static boolean supported(InputDevice device) {
        return device!=null && !device.isVirtual() &&
                (device.supportsSource(InputDevice.SOURCE_GAMEPAD) || device.supportsSource(InputDevice.SOURCE_JOYSTICK));
    }
    private static boolean aynPad(InputDevice device) {
        return supported(device) && device.getVendorId()==0x2020 && device.getProductId()==0x0111;
    }
    private static boolean hasAxis(InputDevice device,int axis) {
        return device.getMotionRange(axis,InputDevice.SOURCE_JOYSTICK)!=null;
    }
    /** D5: an axis as RightStickPicker sees it (its joystick range: min, max, flat, fuzz, source); null when absent. */
    private static RightStickPicker.Axis range(InputDevice device,int axis) {
        final InputDevice.MotionRange r=device.getMotionRange(axis,InputDevice.SOURCE_JOYSTICK);
        return r==null?null:new RightStickPicker.Axis(r.getMin(),r.getMax(),r.getFlat(),r.getFuzz(),
                (r.getSource()&InputDevice.SOURCE_JOYSTICK)==InputDevice.SOURCE_JOYSTICK);
    }
    private static int pickAxis(InputDevice device,int primary,int fallback) {
        return hasAxis(device,primary)?primary:(hasAxis(device,fallback)?fallback:-1);
    }
    /**
     * The device Android has under this number. A number below 0 is never a controller and Android is not asked for
     * it: InputDevice.getDevice(-1) answers with Android's own built-in "Virtual" keyboard on every device, which
     * would then stand on the controller check page as the controller's name.
     */
    private static InputDevice controller(int id) {
        return id<0?null:InputDevice.getDevice(id);
    }
    /** A device with a pad's buttons (a DualShock's touchpad and motion sensors are listed beside its pad without them). */
    private static boolean gamepadButtons(InputDevice device) {
        return device!=null && device.supportsSource(InputDevice.SOURCE_GAMEPAD) && device.hasKeys(KeyEvent.KEYCODE_BUTTON_A)[0];
    }
    private static boolean gamepadButton(int code) {
        return code==KeyEvent.KEYCODE_BUTTON_L2 || code==KeyEvent.KEYCODE_BUTTON_R2 || XboxGamepadMapper.button(code)!=0;
    }
    /** Which controller plays: ControllerChoice (the last one given a button press; at first the best one listed). */
    private void refresh(boolean changed) {
        int[] ids=InputDevice.getDeviceIds();
        int[] ranks=new int[ids.length];
        for(int i=0;i<ids.length;++i) {
            InputDevice candidate=controller(ids[i]);
            ranks[i]=ControllerChoice.rank(supported(candidate),aynPad(candidate),gamepadButtons(candidate));
        }
        int selected=ControllerChoice.pick(deviceId,ids,ranks);
        if(selected==deviceId && !changed)return;
        use(selected);
    }
    private void use(int selected) {
        if(selected!=restDevice){rest.clear();restDecided=false;restDevice=selected;} // (D5: a new controller: its rest is seen again)
        deviceId=selected;
        InputDevice device=controller(deviceId);
        rightAxisX=rightAxisY=leftAxis=rightAxis=-1;
        int buttons=0,axes=0,triggers=0;
        if(device!=null) {
            // Discord D5: the right stick = the pair that looks like a stick and rests at its centre (RightStickPicker); a USB
            // xinput pad without a layout file has its triggers on Z/RZ (resting at -1) and its stick on RX/RY. Thor: Z/RZ + BRAKE/GAS.
            for(int i=0;i<4;++i)candidates[i]=range(device,PICK_AXES[i]);
            pair=RightStickPicker.pick(candidates,restDecided?rest:null);
            if(pair==RightStickPicker.PAIR_Z_RZ){rightAxisX=MotionEvent.AXIS_Z;rightAxisY=MotionEvent.AXIS_RZ;}
            else if(pair==RightStickPicker.PAIR_RX_RY){rightAxisX=MotionEvent.AXIS_RX;rightAxisY=MotionEvent.AXIS_RY;}
            leftAxis=pickAxis(device,MotionEvent.AXIS_LTRIGGER,MotionEvent.AXIS_BRAKE);
            rightAxis=pickAxis(device,MotionEvent.AXIS_RTRIGGER,MotionEvent.AXIS_GAS);
            if(RightStickPicker.zTriggers(pair,candidates,leftAxis>=0 || rightAxis>=0)){leftAxis=MotionEvent.AXIS_Z;rightAxis=MotionEvent.AXIS_RZ;}
            int[] codes={KeyEvent.KEYCODE_DPAD_UP,KeyEvent.KEYCODE_DPAD_DOWN,KeyEvent.KEYCODE_DPAD_LEFT,KeyEvent.KEYCODE_DPAD_RIGHT,
                    KeyEvent.KEYCODE_BUTTON_START,KeyEvent.KEYCODE_BUTTON_SELECT,KeyEvent.KEYCODE_BUTTON_THUMBL,KeyEvent.KEYCODE_BUTTON_THUMBR,
                    KeyEvent.KEYCODE_BUTTON_L1,KeyEvent.KEYCODE_BUTTON_R1,KeyEvent.KEYCODE_BUTTON_MODE,
                    KeyEvent.KEYCODE_BUTTON_A,KeyEvent.KEYCODE_BUTTON_B,KeyEvent.KEYCODE_BUTTON_X,KeyEvent.KEYCODE_BUTTON_Y};
            boolean[] present=device.hasKeys(codes);
            for(int i=0;i<codes.length;++i)if(present[i])buttons|=XboxGamepadMapper.button(codes[i]);
            if(hasAxis(device,MotionEvent.AXIS_HAT_X))buttons|=12;
            if(hasAxis(device,MotionEvent.AXIS_HAT_Y))buttons|=3;
            if(hasAxis(device,MotionEvent.AXIS_X))axes|=1;
            if(hasAxis(device,MotionEvent.AXIS_Y))axes|=2;
            if(rightAxisX>=0)axes|=12;
            boolean[] triggerKeys=device.hasKeys(KeyEvent.KEYCODE_BUTTON_L2,KeyEvent.KEYCODE_BUTTON_R2);
            triggers=(triggerKeys[0]||leftAxis>=0?1:0)|(triggerKeys[1]||rightAxis>=0?2:0);
            description=(aynPad(device)?"AYN built-in controller":"Android gamepad")+" -> Xbox 360, player 1\n"+
                    device.getName()+" (id "+deviceId+")\nRight stick: "+MotionEvent.axisToString(rightAxisX)+" / "+
                    MotionEvent.axisToString(rightAxisY)+(restDecided?" (seen at rest)":"")+"; LT/RT: "+MotionEvent.axisToString(leftAxis)+" / "+MotionEvent.axisToString(rightAxis);
        } else if(QolTouch.padConnected()) { // touch-controls (SPEC 5): the on-screen pad is the controller: every 360 input
            buttons=0xF3FF;axes=15;triggers=3;description="Touch controls (on-screen Xbox 360 pad)";
        } else description="No controller connected";
        QolTouch.controllerPresent(device!=null); // touch-controls: a detected controller hides the on-screen pad
        mapper.configure(leftAxis>=0,rightAxis>=0);
        motionLog.released();scheduleHeldCheck(); // the game was just given a controller at rest
        PrivateTraceActivity.updateGamepadCapabilities(buttons,axes,triggers);
        Log.i(TAG,description.replace('\n',' ')+" capabilities="+Integer.toHexString(buttons)+" axes="+axes+" triggers="+triggers);
        if(controllerListener!=null)controllerListener.accept(deviceId); // (upd1-wave3 android-dualsense: the rumble follows the playing controller)
        publish();
    }
    void clear() {QolTouch.cancelAll();mapper.clear();keyboard.clear();motionLog.released();scheduleHeldCheck();lastEvent="Input released (focus/device change)";publish();}
    private void publish() {
        // qol-touch: the gestures' and the on-screen pad's inputs merged with the controller's (the controller wins input by input)
        // Discord item 10: the keyboard counts as the physical controller (merged before the touch pad's arbitration)
        final int[] k=keyboard.merge(new int[]{mapper.gameButtons(),mapper.leftTrigger(),mapper.rightTrigger(),mapper.leftX,mapper.leftY,mapper.rightX,mapper.rightY});
        final int[] pad=QolTouch.merge(k[0],k[1],k[2],k[3],k[4],k[5],k[6]);
        PrivateTraceActivity.updateGamepad(deviceId>=0 || QolTouch.padConnected() || keyboard.used(),pad[0],pad[1],pad[2],pad[3],pad[4],pad[5],pad[6]);
        if(observer!=null)observer.run();
    }
    boolean key(KeyEvent event) {
        // Test events never release a physically held button, and remain labelled synthetic.
        boolean injected=event.getDeviceId()==-1 && event.isFromSource(InputDevice.SOURCE_GAMEPAD);
        if(!injected && keyboardKey(event))return true;
        // A button press on another connected controller makes that one the player's (a DualShock or an Xbox pad beside a
        // handheld's own pad, a pad that came back under a new number): the game is given it at rest, then this press.
        if(ControllerChoice.adopts(deviceId,event.getDeviceId(),supported(event.getDevice()),gamepadButton(event.getKeyCode()),
                event.getAction()==KeyEvent.ACTION_DOWN,injected))use(event.getDeviceId());
        if(deviceId<0 || (event.getDeviceId()!=deviceId && !injected) ||
                (event.getAction()!=KeyEvent.ACTION_DOWN && event.getAction()!=KeyEvent.ACTION_UP))return false;
        if(!mapper.key(event.getKeyCode(),event.getAction()==KeyEvent.ACTION_DOWN,injected))return false;
        lastEvent=(injected?"SYNTHETIC":"PHYSICAL")+" "+KeyEvent.keyCodeToString(event.getKeyCode())+
                (event.getAction()==KeyEvent.ACTION_DOWN?" down":" up");
        if(event.getRepeatCount()==0)Log.i(TAG,lastEvent);
        publish();return true;
    }
    /** Discord item 10: a key of a real keyboard (KeyboardPad.keyboard) in KeyboardPad's layout; other keys are left to Android. */
    private boolean keyboardKey(KeyEvent event) {
        final InputDevice device=event.getDevice();
        if(device==null || !KeyboardPad.keyboard(device.isVirtual(),device.getKeyboardType()==InputDevice.KEYBOARD_TYPE_ALPHABETIC,supported(device)))return false;
        if(event.getAction()!=KeyEvent.ACTION_DOWN && event.getAction()!=KeyEvent.ACTION_UP)return false;
        final boolean first=!keyboard.used();
        if(!keyboard.key(event.getKeyCode(),event.getAction()==KeyEvent.ACTION_DOWN))return false;
        if(first) { // no controller and no touch pad: the game is given a pad with every 360 input (as the touch pad does)
            if(deviceId<0 && !QolTouch.padConnected())PrivateTraceActivity.updateGamepadCapabilities(0xF3FF,15,3);
            Log.i(TAG,"KEYBOARD plays as the controller: "+device.getName());
        }
        lastEvent="PHYSICAL keyboard "+KeyEvent.keyCodeToString(event.getKeyCode())+(event.getAction()==KeyEvent.ACTION_DOWN?" down":" up");
        if(event.getRepeatCount()==0)Log.i(TAG,lastEvent);
        publish();return true;
    }
    private static float axis(MotionEvent event,int axis,boolean trigger) {
        if(axis<0)return 0;
        InputDevice device=event.getDevice();
        InputDevice.MotionRange range=device==null?null:device.getMotionRange(axis,InputDevice.SOURCE_JOYSTICK);
        if(range==null)return 0;
        float value=event.getAxisValue(axis);
        return trigger?XboxGamepadMapper.trigger(value,range.getMin(),range.getMax(),range.getFlat()):
                XboxGamepadMapper.centered(value,range.getMin(),range.getMax(),range.getFlat());
    }
    boolean motion(MotionEvent event) {
        if(deviceId<0 || event.getDeviceId()!=deviceId || !event.isFromSource(InputDevice.SOURCE_JOYSTICK) ||
                event.getActionMasked()!=MotionEvent.ACTION_MOVE)return false;
        if(!restDecided){ // D5: where Z/RZ/RX/RY rest decides the right stick once (a pad whose triggers are Z/RZ rests them at -1)
            for(int i=0;i<4;++i)rest.observe(i,event.getAxisValue(PICK_AXES[i]),candidates[i]);
            if(rest.decided(candidates)){restDecided=true;final int was=pair;
                if(RightStickPicker.pick(candidates,rest)!=was){use(deviceId);
                    Log.i(TAG,"right stick re-picked from where the axes rest: "+RightStickPicker.name(was)+" -> "+RightStickPicker.name(pair));}}
        }
        mapper.motion(axis(event,MotionEvent.AXIS_X,false),axis(event,MotionEvent.AXIS_Y,false),
                axis(event,rightAxisX,false),axis(event,rightAxisY,false),
                axis(event,MotionEvent.AXIS_HAT_X,false),axis(event,MotionEvent.AXIS_HAT_Y,false),
                axis(event,leftAxis,true),axis(event,rightAxis,true));
        lastEvent="PHYSICAL stick / D-pad / trigger motion";
        // The one log line of stick-only play (button presses have their own lines in key()): PhysicalMotionLog decides.
        if(motionLog.moved(event.getDeviceId(),deviceId,mapper.leftX,mapper.leftY,mapper.rightX,mapper.rightY,mapper.physicalHat(),
                mapper.physicalLeftTrigger(),mapper.physicalRightTrigger(),event.getEventTime()))Log.i(TAG,PhysicalMotionLog.LINE);
        scheduleHeldCheck();
        publish();return true;
    }
    /** Asks again at the time the next line of a held stick falls due; nothing is scheduled while everything rests. */
    private void scheduleHeldCheck() {
        long due=motionLog.nextLineAt();
        if(due==heldCheckAt)return;
        motionTimer.removeCallbacks(heldCheck);
        heldCheckAt=due;
        if(due!=Long.MIN_VALUE)motionTimer.postAtTime(heldCheck,due);
    }
    private void heldCheck() {
        heldCheckAt=Long.MIN_VALUE;
        if(motionLog.stillHeld(SystemClock.uptimeMillis()))Log.i(TAG,PhysicalMotionLog.LINE);
        scheduleHeldCheck();
    }
    void close() {if(manager!=null)manager.unregisterInputDeviceListener(this);controllerListener=null;deviceId=-1;clear();motionTimer.removeCallbacks(heldCheck);}
    @Override public void onInputDeviceAdded(int id){refresh(false);}
    @Override public void onInputDeviceRemoved(int id){refresh(id==deviceId);}
    @Override public void onInputDeviceChanged(int id){refresh(id==deviceId);}
}
