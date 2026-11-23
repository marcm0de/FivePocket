package dev.recompbootstrap.smoke;

import android.app.Activity;
import android.app.ActivityManager;
import android.content.pm.ApplicationInfo;
import android.graphics.Color;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import android.os.Process;
import android.system.Os;
import android.system.OsConstants;
import android.util.Log;
import android.widget.TextView;
import android.widget.FrameLayout;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.Surface;
import android.view.SurfaceHolder;
import android.view.SurfaceView;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.view.KeyEvent;
import android.view.MotionEvent;
import java.io.InputStream;
import java.io.ByteArrayOutputStream;
import java.io.FileDescriptor;
import java.io.FileOutputStream;
import java.security.MessageDigest;

/**
 * The game window. No network. Compiled into every build type since ship week (it was debug-only before, and
 * refused to start unless the app was debuggable; that one condition is gone).
 *
 * What is drawn over the game is GameWindowStatus's decision: no build draws a developer label for any start, only
 * two plain lines when a start was refused or the game has stopped. The debug build draws its developer label
 * again while the switch file files/private-trace/diag-window-banner exists (looked for once, in onCreate).
 * Select held, then A, takes a picture of the game (CaptureController).
 *
 * What still guards a start, in the order they are asked: the dedicated process (the single-instance rule), a
 * well-formed request (mode, 64-digit SHA-256 of the game code, 32-digit run id, 64-digit SHA-256 of the start
 * arguments), a run id that was never used, and then, on one open descriptor, the loader's file rule and the game
 * code's SHA-256 against the one the request names. In the shipped build the launcher is the only caller (the
 * activity is not exported) and names the SHA-256 it has just compared with settings/qualified-code.txt, which
 * ShipFirstRun wrote from the record inside the APK.
 */
public final class PrivateTraceActivity extends Activity {
    private static final String TAG="RecompPrivateTrace";
    private boolean privateProcess;
    private TextView status;
    private boolean gameRequested;
    private boolean previewRequested;
    private boolean liveRequested;
    private int frameMode;
    // BUG 02: onStop ends the process GRACE_MS later instead of at once; onStart takes the end back (BackgroundGrace)
    private final android.os.Handler mainThread=new android.os.Handler(android.os.Looper.getMainLooper());
    private long stoppedAt=-1;
    private final BackgroundGrace grace=new BackgroundGrace(new BackgroundGrace.Timer(){
        public void post(Runnable r,long delayMs){mainThread.postDelayed(r,delayMs);}
        public void cancel(Runnable r){mainThread.removeCallbacks(r);}
    },()->{if(!privateProcess)return;Log.i(TAG,"BACKGROUND_GRACE end pid="+Process.myPid());Process.killProcess(Process.myPid());});
    private boolean frameSurfaceAttached;
    private Runnable startRunner;
    private PrivateGamepadInput gamepadInput;
    private Haptics haptics; // Update 1 haptics: the game's rumble on the device's motor (null: no game start)
    private InputDispatchOption inputDispatch; // null until a request was accepted; see InputDispatchOption
    private boolean closedUsedLauncherRequest; // see InputDispatchOption.closesUsedRequest
    // A start the launcher made (a player pressed Play): its refusal or stop is left for the launcher. Script starts: false.
    private boolean launcherStart;
    // No developer label in this window (GameWindowStatus.plainWindow: every build, unless the debug build's switch file exists).
    private boolean plainWindow;
    private String acceptedLauncherRequest; // the launcher's request once this window has claimed it; null before, and for scripts
    private boolean leaveWithB; // a refused or stopped start in a plain window or made by the launcher: B closes the window
    private CaptureController capture; // Select held + A: a picture of the game (CaptureController); windows with a picture only
    static native void updateGamepad(boolean connected,int buttons,int leftTrigger,int rightTrigger,
            int leftX,int leftY,int rightX,int rightY);
    static native void updateGamepadCapabilities(int buttons,int axes,int triggers);
    static native void setGamepadLatch(boolean on); // 2026-11-09: the press latch (PadLatch, native_gamepad_snapshot.h)
    static native int[] readGamepadForDiagnostics();
    static native int readTouchContext(); // qol-touch: the library's recomp_touch_context_v1 (-2: absent)
    // side-menu: the trainer (private_trace_jni.cpp; -100 / null without the library's export)
    static native int trainerRequest(int action,int a0,int a1,int a2);
    static native int trainerState();
    static native String trainerStatus();
    static native int imenu(int op); // Update 1 (Quick Menu Style): recomp_imenu_v1; -100 without the export
    static native int cameraTurn(float dYawDeg,float dPitchDeg); // Update 1 touch item 4: recomp_camera_turn_v1; -100 without the export
    static native int aimAssist(int mode); // Update 1 touch item 3: recomp_aim_assist_v1 (0 game, 1 strong); -100 without the export
    static native int trainerList(int list,int index,String[] out);
    static native String modsStatus();
    static native int phoneState(); // v717: the library's recomp_phone_state_v1 (bit 0 the phone is up; -100 absent; SPEC 3.15)
    static native int readFps(float[] out); // v714: the library's recomp_fps_v1 ({shown, real, ms}; 1 counter on, 0 off, -100 absent)
    // Update 1 P6 My Music (my_music.h): recomp_my_music_v1 (0 idle, 1 playing, 2 paused) and recomp_my_music_state_v1 (-1 before the
    // game's memory exists); both -100 when the library has no My Music. MyMusicPlayer calls them from its own thread.
    static native int myMusicReport(int state);
    static native int myMusicState();
    private MyMusicPlayer music; // (Update 1 P6: null until the game window is set up for a game)

    /** 2026-11-17: setup's menu-sound pass covers this window with its page and ends the game process (MenuSoundPassOverlay); null otherwise. */
    private MenuSoundPassOverlay menuSoundPass;
    @Override public boolean dispatchKeyEvent(KeyEvent event) {
        if(menuSoundPass!=null && menuSoundPass.key(event))return true;
        if(sideMenuKey(event))return true; // side-menu
        // Nothing runs that could want the button: B goes back to the launcher, as the window's text says.
        if(leaveWithB && event.getKeyCode()==KeyEvent.KEYCODE_BUTTON_B){if(event.getAction()==KeyEvent.ACTION_UP)finish();return true;}
        if(capture!=null && capture.key(event))return true;
        return (gamepadInput!=null && gamepadInput.key(event)) || super.dispatchKeyEvent(event);
    }
    /** upd1-wave3 android-dualsense: a controller's touchpad (a DualSense's: Android's pointer, source mouse / touchpad) is never a
     *  finger on this window (PadTouchpad); touchscreen events pass as before. One log line per window the first time. */
    @Override public boolean dispatchTouchEvent(MotionEvent event) {
        final InputDevice device=event.getDevice();
        if(device!=null && PadTouchpad.ignore(event.getSource(),device.getVendorId())) {
            if(!touchpadNoted){touchpadNoted=true;Log.i(TAG,"CONTROLLER_TOUCHPAD ignored source=0x"+Integer.toHexString(event.getSource())+" device="+device.getName());}
            return true;
        }
        return super.dispatchTouchEvent(event);
    }
    private boolean touchpadNoted;
    @Override public boolean dispatchGenericMotionEvent(MotionEvent event) {
        if(menuSoundPass!=null)return true; // (the pass: the game gets no input)
        if(sideMenu!=null && sideMenu.isOpen() && (event.getSource()&InputDevice.SOURCE_JOYSTICK)==InputDevice.SOURCE_JOYSTICK){sideMenuHat(event);return true;} // side-menu
        return (gamepadInput!=null && gamepadInput.motion(event)) || super.dispatchGenericMotionEvent(event);
    }
    @Override public void onWindowFocusChanged(boolean focused) {
        super.onWindowFocusChanged(focused);
        if(!focused && gamepadInput!=null)gamepadInput.clear();
        if(focused)fullPanel(); // (bars shown by a swipe hide again)
        if(inputDispatch!=null && inputDispatch.takeRequest(focused))requestUnbufferedJoystick();
    }
    /**
     * Low-delay stick input (files/private-trace/diag-lt2-unbuffered, off unless that file exists): asks the window's
     * root view to deliver joystick MotionEvents (sticks, triggers, HAT) as they arrive instead of holding them until
     * the next frame callback. Reached only through InputDispatchOption.takeRequest, which is true at most once per
     * run and never below API 30. Buttons are KeyEvents and are not affected.
     */
    @android.annotation.TargetApi(30)
    private void requestUnbufferedJoystick(){
        getWindow().getDecorView().requestUnbufferedDispatch(InputDevice.SOURCE_CLASS_JOYSTICK);
        Log.i(InputDispatchOption.LOG_TAG,InputDispatchOption.LOG_LINE);
    }
    /** Looks for the low-delay input switch file once; called from onCreate only, after the request was accepted. */
    private void readInputDispatchOption(){
        inputDispatch=InputDispatchOption.forRun(gameRequested,getFilesDir(),android.os.Build.VERSION.SDK_INT);
    }
    /** Live game windows use the whole panel: system bars hidden, shown transiently by an edge swipe. */
    private void fullPanel(){
        if(!liveRequested)return;
        if(android.os.Build.VERSION.SDK_INT>=28)getWindow().getAttributes().layoutInDisplayCutoutMode=
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
        if(android.os.Build.VERSION.SDK_INT>=30){
            getWindow().setDecorFitsSystemWindows(false);
            WindowInsetsController controller=getWindow().getInsetsController();
            if(controller!=null){
                controller.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
                controller.hide(WindowInsets.Type.systemBars());
            }
        }else getWindow().getDecorView().setSystemUiVisibility(View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY|View.SYSTEM_UI_FLAG_FULLSCREEN|
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION|View.SYSTEM_UI_FLAG_LAYOUT_STABLE|View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION|
                View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN);
    }
    @Override protected void onDestroy() {
        mainThread.removeCallbacks(sessionWatch); // (Update 1)
        if(music!=null){music.release();music=null;} // (Update 1 P6: idle to the game, the player released)
        if(capture!=null){capture.close();capture=null;}
        if(gamepadInput!=null){gamepadInput.close();gamepadInput=null;}
        QolTouch.appActions=null;if(trainerSound!=null){trainerSound.release();trainerSound=null;} // (SPEC 3.14)
        QolTouch.close(); // qol-touch
        if(haptics!=null){haptics.stop();haptics=null;} // (Update 1 haptics: motor off, the library unbound)
        if(screenTest!=null){try{unregisterReceiver(screenTest);}catch(IllegalArgumentException gone){}screenTest=null;} // qol-fill (test)
        super.onDestroy();
        // A destroyed game window never comes back (a new one refuses the used request), so the process ends here: a used
        // launcher request closed before it was shown (onStop did not run), or the system destroyed a stopped window during
        // its grace (BUG 02). A finishing window already ended in onStop.
        if(privateProcess)Process.killProcess(Process.myPid());
    }

    private static native boolean prepareProcess(boolean traceRun);
    private static native int claimRequest(String filesDirectory, String requestId);
    private static native int openLibrary(String filesDirectory,int frameMode);
    private static native int checkAbi(int descriptor, String filesDirectory);
    private static native int openArguments(String filesDirectory, String requestId);
    private static native byte[] runTrace(int descriptor, String filesDirectory, String requestId, byte[] manifest,int frameMode);
    // Update 1 (Sandbox): the play mode runTrace hands the game code before the game starts (play_mode_gate.h), and that gate's refusal
    private static native void setPlayMode(int mode);
    private static native int playModeRefusal();
    private static native boolean attachFrameSurface(Surface surface,int width,int height,boolean reject,boolean failAfterPresent,boolean rejectSecond);
    private static native void resizeFrameSurface(int width,int height);
    private static native void detachFrameSurface();
    private static native void finishCheck();

    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        // Decided once, before anything is shown: no developer label, unless this is the debug build and its switch file exists.
        plainWindow=GameWindowStatus.plainWindow((getApplicationInfo().flags&ApplicationInfo.FLAG_DEBUGGABLE)!=0,getFilesDir());
        status=new TextView(this);
        status.setTextSize(20);
        status.setTextColor(Color.WHITE);
        status.setBackgroundColor(Color.rgb(24,28,36));
        status.setPadding(40,40,40,40);
        setContentView(status);
        if(plainWindow){ // black until the game draws, instead of the theme's window and the grey status page
            getWindow().setBackgroundDrawable(new android.graphics.drawable.ColorDrawable(Color.BLACK));
            status.setBackgroundColor(Color.BLACK);status.setGravity(Gravity.CENTER);status.setVisibility(View.GONE);
        }
        ActivityManager manager=(ActivityManager)getSystemService(ACTIVITY_SERVICE);
        if(manager!=null && manager.getRunningAppProcesses()!=null){
            for(ActivityManager.RunningAppProcessInfo info:manager.getRunningAppProcesses()){
                if(info.pid==Process.myPid() && info.uid==Process.myUid() &&
                        (getPackageName()+":private_trace").equals(info.processName))privateProcess=true;
            }
        }
        String mode=getIntent().getStringExtra("private_trace");
        String expected=getIntent().getStringExtra("expected_sha256");
        String requestId=getIntent().getStringExtra("request_id");
        String manifestHash=getIntent().getStringExtra("manifest_sha256");
        String playMode=getIntent().getStringExtra("play_mode"); // Update 1: "story" / "sandbox" from the launcher; none from a script
        boolean displayRequested="run-native-display".equals(mode) || "run-native-display-reject".equals(mode) ||
                "run-native-display-reject-second".equals(mode) || "run-native-display-post-accept-fail".equals(mode);
        boolean failAfterPresent="run-native-swap-post-accept-fail".equals(mode) || "run-native-display-post-accept-fail".equals(mode);
        boolean rejectSecond="run-native-display-reject-second".equals(mode);
        liveRequested="run-native-swap".equals(mode) || "run-native-swap-reject".equals(mode) || failAfterPresent || displayRequested;
        boolean rejectPreview="run-stopped-preview-reject".equals(mode) || "run-native-swap-reject".equals(mode) ||
                "run-native-display-reject".equals(mode);
        previewRequested="run-stopped-preview".equals(mode) || rejectPreview || liveRequested;
        frameMode=displayRequested?4:(liveRequested?3:(previewRequested?2:0));
        gameRequested="run-trace".equals(mode) || previewRequested;
        if(!privateProcess ||
                !("abi-check".equals(mode) || gameRequested) || expected==null || !expected.matches("[0-9a-f]{64}") ||
                requestId==null || !requestId.matches("[0-9a-f]{32}") ||
                (gameRequested && (manifestHash==null || !manifestHash.matches("[0-9a-f]{64}"))) || !PlayMode.accepted(playMode)){
            show("REJECT_REQUEST");return;
        }
        // closesUsedRequest(1,...) is true exactly when the id is a line of settings/launcher-runs.txt.
        launcherStart=InputDispatchOption.closesUsedRequest(1,getFilesDir(),requestId);
        System.loadLibrary("recomp_private_debug");
        setPlayMode(PlayMode.nativeValue(playMode)); // (before the runner: runTrace hands it to the game code before the game starts)
        QolTouch.sandboxLaunch=PlayMode.SANDBOX.equals(playMode); // (1.1.5 SPEC 3.17: the Interaction Menu button's fallback)
        if(gameRequested)setGamepadLatch(PadLatch.on(getFilesDir())); // (2026-11-09: a tap shorter than one game frame reaches the game)
        if(gameRequested)gamepadInput=new PrivateGamepadInput(this);
        if(gameRequested)getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        int claimed=claimRequest(getFilesDir().getAbsolutePath(),requestId);
        if(InputDispatchOption.closesUsedRequest(claimed,getFilesDir(),requestId)){
            // Play, Home, back to the app: Android restarted this activity with the request that was already used.
            // Return to the launcher instead of a refusal screen the gamepad cannot leave. Script starts never get here.
            Log.i(TAG,InputDispatchOption.CLOSED_LINE+" pid="+Process.myPid());
            closedUsedLauncherRequest=true;finish();return;
        }
        if(claimed!=0){show(claimed==1?"REJECT_USED_REQUEST":"REJECT_REQUEST_STORAGE");return;}
        if(launcherStart)acceptedLauncherRequest=requestId; // from here on a refusal is reported to the launcher (show)
        if(!prepareProcess(gameRequested)){show("REJECT_PROCESS");return;}
        readInputDispatchOption();
        // Update 1 haptics (an accepted game start only): its thread waits for the game library (rumble_port.h)
        if(gameRequested)haptics=Haptics.start(this,getFilesDir());
        if(haptics!=null && gamepadInput!=null)gamepadInput.onController(haptics::controller); // (upd1-wave3 android-dualsense: rumble on the playing controller)
        startRunner=()->{
            if(!grace.firstStart())return; // (the surface attaches again after a return from the background: one runner only)
            show("CHECKING_PRIVATE_LIBRARY");
            new Thread(()->{
                String result;
                try {result=verifyAndCheck(expected,requestId,manifestHash);}
                catch(Exception error){result="REJECT_IO_"+error.getClass().getSimpleName();}
                finally {finishCheck();}
                String completed=result;
                runOnUiThread(()->show(completed));
            },"private-library-check").start();
        };
        if(previewRequested){
            FrameLayout content=new FrameLayout(this);
            SurfaceView surface=new SurfaceView(this);
            surface.getHolder().addCallback(new SurfaceHolder.Callback(){
                private void attach(SurfaceHolder holder,int width,int height){
                    if(frameSurfaceAttached || width<1 || height<1)return;
                    frameSurfaceAttached=attachFrameSurface(holder.getSurface(),width,height,rejectPreview,failAfterPresent,rejectSecond);
                    if(frameSurfaceAttached)startRunner.run();
                    else {show("REJECT_SURFACE");finishCheck();}
                }
                @Override public void surfaceCreated(SurfaceHolder holder){
                    attach(holder,holder.getSurfaceFrame().width(),holder.getSurfaceFrame().height());
                }
                @Override public void surfaceChanged(SurfaceHolder holder,int format,int width,int height){
                    if(frameSurfaceAttached)resizeFrameSurface(width,height);else attach(holder,width,height);
                }
                @Override public void surfaceDestroyed(SurfaceHolder holder){
                    if(frameSurfaceAttached){detachFrameSurface();frameSurfaceAttached=false;}
                }
            });
            if(gameRequested)screenSurface(content,surface); // qol-fill: Settings > Display > Screen (ScreenFill)
            else content.addView(surface,new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.MATCH_PARENT));
            // qol-touch: long presses for the map and the wheels. qol-fill: on the content (the screen) rather than the surface, which in
            // Fill lies past the screen's edges: touches keep the screen's coordinates in Fit and Fill alike (nothing above takes touches).
            if(gameRequested)content.setOnTouchListener(QolTouch.open(getFilesDir()));
            final View touchPad=gameRequested?QolTouch.overlay(this):null; // touch-controls: the drawn on-screen pad (takes no touches itself)
            if(touchPad!=null)content.addView(touchPad,new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.MATCH_PARENT));
            if(gameRequested)content.addView(new FpsLabelView(this,()->fillNow),new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT)); // v714: the frame-rate label (the owner: "minimalistic and much smaller"; 1.2.1: restored, "I like the old minimalist fps counter")
            if(gameRequested)setupSideMenu(content,surface); // side-menu (SPEC 5): over the pad
            ((ViewGroup)status.getParent()).removeView(status);
            status.setTextSize(11);status.setPadding(12,6,12,6);status.setBackgroundColor(0x99000000);
            content.addView(status,new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,ViewGroup.LayoutParams.WRAP_CONTENT,
                    plainWindow?Gravity.CENTER:Gravity.TOP|Gravity.LEFT));
            capture=CaptureController.attach(this,surface,content,e->gamepadInput!=null && gamepadInput.key(e));
            holdSelectSetting(); // (Update 1: hold Select opens the side menu, the side menu's own setting)
            mainThread.removeCallbacks(sessionWatch);mainThread.postDelayed(sessionWatch,1000); // (Update 1: a local session yields hold Select)
            setContentView(content);fullPanel();if(gameRequested)menuSoundPass=MenuSoundPassOverlay.attach(this,content);show(liveRequested?"WAITING_FOR_NATIVE_SWAP_SURFACE":"WAITING_FOR_STOPPED_PREVIEW_SURFACE");
        }else startRunner.run();
    }

    // qol-fill (ScreenFill): the setting; the surface laid out to cover the screen in Fill (again whenever the content's size changes: a
    // fold, insets) with its buffer, or the screen's size in Fit; and the visible fraction for the library (1 1 in Fit).
    private boolean fillNow,fillNative;
    private boolean screenFill(){
        try{return ScreenFill.wanted(java.nio.file.Files.readAllBytes(new java.io.File(getFilesDir(),ScreenFill.SETTING).toPath()));}
        catch(Exception none){return false;}
    }
    private void screenSurface(FrameLayout content,SurfaceView surface){
        fillNow=screenFill();fillNative=new java.io.File(getFilesDir(),ScreenFill.NATIVE).exists();
        final android.graphics.Rect bounds=android.os.Build.VERSION.SDK_INT>=30?getWindowManager().getCurrentWindowMetrics().getBounds():
                new android.graphics.Rect(0,0,getResources().getDisplayMetrics().widthPixels,getResources().getDisplayMetrics().heightPixels);
        if(fillNow){ // (sized before the surface exists: it is made at the buffer's size)
            final int[] start=ScreenFill.layout(bounds.width(),bounds.height(),true),buffer=ScreenFill.buffer(bounds.width(),bounds.height(),fillNative);
            surface.getHolder().setFixedSize(buffer[0],buffer[1]);
            content.addView(surface,new FrameLayout.LayoutParams(start[0],start[1],Gravity.CENTER));
        } else content.addView(surface,new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.MATCH_PARENT,Gravity.CENTER));
        applyScreen(surface,bounds.width(),bounds.height());
        content.addOnLayoutChangeListener((v,l,top,r,b,ol,ot,or,ob)->{
            if(r-l!=or-ol || b-top!=ob-ot)surface.post(()->applyScreen(surface,r-l,b-top));
        });
        // TEST ONLY (the Fill A/B in one run, the Fold's run-to-run spread being +-3 ms; nothing written to the device): in a debuggable
        // build, a broadcast from the shell (it holds DUMP; apps do not) switches Fit / Fill while the game runs:
        // adb shell am broadcast -a dev.recompbootstrap.smoke.SCREEN_TEST -p <package> --ez fill true|false
        if((getApplicationInfo().flags&ApplicationInfo.FLAG_DEBUGGABLE)!=0) {
            screenTest=new android.content.BroadcastReceiver(){
                @Override public void onReceive(android.content.Context c,android.content.Intent i){
                    final String m=i.getStringExtra("menu"); // side-menu (TEST ONLY): --es menu open|alert|close, for screenshots
                    if(m!=null && sideMenu!=null){
                        if(m.equals("open"))sideMenu.openMenu();else if(m.equals("alert"))sideMenu.alertForTest();else if(m.equals("close"))sideMenu.close();
                        menuSource="test broadcast";menuChanged();Log.i("RecompQolTouch","SIDE_MENU test "+m);return;
                    }
                    if(!i.hasExtra("fill"))return;
                    fillNow=i.getBooleanExtra("fill",false);applyScreen(surface,content.getWidth(),content.getHeight());
                    Log.i("RecompQolTouch","QOL_SCREEN test fill="+(fillNow?1:0));
                }};
            registerReceiver(screenTest,new android.content.IntentFilter("dev.recompbootstrap.smoke.SCREEN_TEST"),"android.permission.DUMP",null,
                    android.os.Build.VERSION.SDK_INT>=33?android.content.Context.RECEIVER_EXPORTED:0);
        }
    }
    private android.content.BroadcastReceiver screenTest;

    // ---- side-menu (SPEC 5): the menu, its rows from the saved settings, what its events do
    private SideMenu sideMenu;private SideMenuView sideMenuView;private SideMenuPad sideMenuPad;private boolean padMenuWasOpen; // (Update 1 QoL: the pad)
    private String menuSource="touch input"; // (the log's label for the menu's next events: touch, the pad / Back, or the shell's test broadcast)
    private void setupSideMenu(FrameLayout content,SurfaceView surface){
        final java.io.File files=getFilesDir();
        final SideMenuRows rows=new SideMenuRows();
        if(MyMusicSettings.SHOWN){ // (Update 1: My Music waits for E-SR1; no player, no side-menu tab meanwhile)
            music=new MyMusicPlayer(this,new MyMusicPlayer.Game(){public int state(){return myMusicState();}public int report(int s){return myMusicReport(s);}}); // (Update 1 P6)
            rows.music=music;
        }
        final SettingsStore store=new SettingsStore(files,()->false); // (the game is the caller: its live files are polled)
        rows.trainer=new SideMenuRows.Trainer(){
            public boolean present(){return trainerState()!=-100;}
            public int state(){final int s=trainerState();return s==-100?0:s;}
            public int count(int list){return Math.max(0,trainerList(list,-1,null));}
            public String label(int list,int i){final String[] out=new String[1];trainerList(list,i,out);return out[0];}
            public String status(){final String s=trainerStatus();return s==null?"":s;}
            public String mods(){final String s=modsStatus();return s==null?"":s;}
        };
        sideMenu=new SideMenu(rows);sideMenuView=new SideMenuView(this,sideMenu);menuRows=rows;
        sideMenuPad=new SideMenuPad(sideMenu,PadGlue.timer(),this::menuChanged); // (Update 1 QoL)
        // Update 1 QoL: the favourites and where the menu was left, from the last session
        rows.favourites.load(MenuFiles.read(files,MenuFiles.FAVOURITES));rows.loadMenuWords(MenuFiles.read(files,MenuWords.FILE),MenuFiles.read(files,MenuWords.QUICK_FILE)); // (Update 1)
        QolTouch.camera(rows.cameraSwipe,false,rows.camSpeed,rows.camInvert); // (item 4: the direct turn once the capability scan has seen the export: the ready handler; 2026-11-09: Swipe before it is the speed stick)
        QolTouch.aimMode(rows.aimTap,rows.autoFire); // (2026-11-09: Aim Mode, Auto-Fire)
        QolTouch.aim(rows.adsTurns,rows.fireAims); // (Update 1 touch item 3; the library reads menu.txt's aim-assist word itself at its first gameplay tick, the app calls recomp_aim_assist_v1 on a change)
        sideMenu.restore(MenuFiles.read(files,MenuFiles.MAIN)); // (Update 1 QoL: where the menu was left; 2026-11-09: was inside the line above's comment, so it never ran)
        sideMenuView.source(surface); // (menu-redesign: the game's picture, blurred, behind the menu)
        sideMenu.controls(QolTouch::controlAt); // (the HIDE eye inside the edge strip stays the pad's)
        content.addView(sideMenuView,new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,ViewGroup.LayoutParams.MATCH_PARENT));
        final boolean inputOption=android.os.Build.VERSION.SDK_INT>=30;
        new Thread(()->{ // (the capability scan may read the library once, ~1 s)
            try{
                final Capabilities caps=CapabilityScanner.load(files,inputOption);
                final Settings saved=store.load(caps).settings;
                runOnUiThread(()->{rows.capabilities=caps;rows.settings=saved;QolTouch.camera(rows.cameraSwipe,caps.cameraTurn(),rows.camSpeed,rows.camInvert); /* (Update 1 touch item 4) */ if(music!=null)music.settings(saved);Log.i("RecompQolTouch","SIDE_MENU ready trainer="+(rows.trainer.present()?1:0));});
            }catch(Exception e){Log.w("RecompQolTouch","SIDE_MENU settings not loaded: "+e);}
        },"side-menu-load").start();
        final SideMenuApply apply=new SideMenuApply(rows,new SideMenuApply.Effects(){
            public boolean save(Settings s){
                final SettingsStore.SaveResult r=store.save(s,rows.capabilities);
                Log.i("RecompQolTouch","SIDE_MENU save ok="+r.ok+" written="+r.written+" removed="+r.removed+(r.error!=null?" error="+r.error:""));
                return r.ok;
            }
            public void touchSettingsChanged(Settings s){QolTouch.reloadSettings(files);}
            public void touchShown(boolean shown){QolTouch.shown(shown);}
            public void screenFill(boolean fill){fillNow=fill;applyScreen(surface,content.getWidth(),content.getHeight());}
            public void hapticsChanged(int mode){if(haptics!=null)haptics.setMode(mode);} // (Update 1 haptics)
            public void menuWords(int hold){Log.i("RecompQolTouch","SIDE_MENU menu.txt hold="+hold+" written="+MenuFiles.menuWords(files,hold));}
            public void copyDiagnostics(java.util.function.Consumer<String> done){ // (Update 1: off the UI thread, minimum priority)
                final Thread t=new Thread(()->{
                    String text;try{text=DiagnosticsAndroid.text(PrivateTraceActivity.this);}catch(RuntimeException|LinkageError e){text=null;}
                    final String report=text;
                    runOnUiThread(()->{
                        done.accept(report==null?Diagnostics.failed("the report could not be made"):DiagnosticsAndroid.deliver(PrivateTraceActivity.this,report,false));
                        Log.i("RecompQolTouch","SIDE_MENU diagnostics "+(report==null?"failed":"copied chars="+report.length()));
                        if(sideMenuView!=null)sideMenuView.invalidate();});
                },"diagnostics");
                t.setPriority(Thread.MIN_PRIORITY);t.start();
            }
            public int gameInteractionMenu(){final int r=imenu(5);Log.i("RecompQolTouch","SIDE_MENU game interaction menu -> "+r);return r;}
            public void quickMenuStyle(int style){final boolean w=MenuFiles.quickMenu(files,style);
                Log.i("RecompQolTouch","SIDE_MENU quick-menu.txt style="+style+" written="+w+" imenu="+imenu(MenuWords.imenuOp(style)));}
            public void touchCamera(boolean swipe,int speed,boolean invert){
                final boolean w=MenuFiles.touchCamera(files,swipe,speed,invert);QolTouch.camera(swipe,rows.capabilities.cameraTurn(),speed,invert);
                Log.i("RecompQolTouch","SIDE_MENU touch camera swipe="+swipe+" speed="+speed+" invert="+invert+" written="+w);}
            public void touchAimMode(boolean tap,boolean auto){ // (2026-11-09)
                final boolean w=MenuFiles.touchAimMode(files,tap,auto);QolTouch.aimMode(tap,auto);
                Log.i("RecompQolTouch","SIDE_MENU touch aim mode tap="+tap+" autofire="+auto+" written="+w);}
            public void touchAim(boolean ads,boolean fire,int assist,boolean assistChanged){
                final boolean w=MenuFiles.touchAim(files,ads,fire,assist);QolTouch.aim(ads,fire);
                Log.i("RecompQolTouch","SIDE_MENU touch aim ads="+ads+" fire="+fire+" assist="+assist+" written="+w+(assistChanged?" library="+aimAssist(assist):""));}
            public void trainerEnabled(boolean on){
                try(FileOutputStream out=new FileOutputStream(new java.io.File(files,"settings/trainer.txt"))){
                    out.write((on?"trainer=on\n":"trainer=off\n").getBytes(java.nio.charset.StandardCharsets.US_ASCII));}
                catch(java.io.IOException e){Log.w("RecompQolTouch","SIDE_MENU trainer.txt: "+e);}
                Log.i("RecompQolTouch","SIDE_MENU trainer="+(on?"on":"off")+" sync="+trainerRequest(SideMenuApply.SYNC,0,0,0));
            }
            public int trainer(int action,int a0,int a1){
                final int r=trainerRequest(action,a0,a1,0);Log.i("RecompQolTouch","SIDE_MENU trainer action="+action+" a0="+a0+" a1="+a1+" -> "+r);return r;}
        });
        // SPEC 3.14 (Fold ship 2026-10-25, the Thor's lower-screen buttons on one screen): SAVE asks the library for the game's Save Game
        // menu; TRAINER (shown while the trainer is on) laughs (the owner's sound, debug assets) and opens this menu on the Trainer tab.
        trainerSound=new TrainerSound(this);
        QolTouch.appActions=id->{
            if(id.equals("nextweapon")){Log.i("RecompQolTouch","NEXT_WEAPON request -> "+trainerRequest(TouchPad.NEXT_WEAPON_ACTION,0,0,0));return;} // (Update 1 touch item 2; ALLOCATION 13)
            if(id.equals("qsave")){Log.i("RecompQolTouch","QUICK_SAVE request -> "+trainerRequest(SideMenuApply.OPEN_SAVE_MENU,0,0,0));return;}
            if(id.equals("trainer") && sideMenu!=null){
                if(trainerSound!=null)trainerSound.play();
                if(!sideMenu.isOpen())sideMenu.openMenu();
                sideMenu.toTab(SideMenuRows.trainerTab(rows.tabs(false)));menuSource="trainer button";menuChanged();
            }
        };
        QolTouch.attachMenu(sideMenu,sideMenuView,()->{
            for(SideMenu.Event e:sideMenu.take()){
                Log.i("RecompQolTouch","SIDE_MENU "+e+" ("+menuSource+")");
                if(e.type==SideMenu.Event.Type.OPENED && gamepadInput!=null)gamepadInput.clear(); // (the game is let go of every pad input)
                apply.apply(e);
                if(e.type==SideMenu.Event.Type.CLOSED)MenuFiles.write(files,MenuFiles.MAIN,sideMenu.place()); // (Update 1 QoL: where it was left)
            }
            if(rows.favourites.changed){rows.favourites.changed=false;MenuFiles.write(files,MenuFiles.FAVOURITES,rows.favourites.save());}
            sideMenuView.invalidate();menuSource="touch input";
            watchOnline(apply);
            holdSelectSetting(); // (Update 1: the setting may have changed in the menu)
        });
    }
    /** The touchscreen's Back (Android's edge gesture) opens the menu or goes back in it; while the menu is open a gamepad drives it
     *  (D-pad, A, B, START) and the game gets none of the pad's keys. */
    private boolean sideMenuKey(KeyEvent e){
        if(sideMenu==null)return false;
        final boolean pad=(e.getSource()&InputDevice.SOURCE_GAMEPAD)==InputDevice.SOURCE_GAMEPAD || (e.getSource()&InputDevice.SOURCE_DPAD)==InputDevice.SOURCE_DPAD;
        final int k=e.getKeyCode();
        if(k==KeyEvent.KEYCODE_BACK && !pad){
            if(e.getAction()==KeyEvent.ACTION_UP){if(sideMenu.isOpen())sideMenu.back();else sideMenu.openMenu();menuSource="Back key input";menuChanged();}
            return true;
        }
        if(!sideMenu.isOpen() || !pad)return false;
        // Update 1 QoL (menus plan A2): the pad through SideMenuPad (NavInput: keys, HAT, left stick, hold-to-repeat; A's hold rule)
        if(sideMenuPad!=null){menuSource="pad input";sideMenuPad.key(k,e.getAction()==KeyEvent.ACTION_DOWN,e.getRepeatCount(),e.getEventTime());}
        return true;
    }
    /** A D-pad that reports as the hat axes, while the menu is open: one step per press. */
    private void sideMenuHat(MotionEvent e){
        if(sideMenuPad!=null){menuSource="pad input";sideMenuPad.motion(PadGlue.motion(e));} // (Update 1 QoL: the HAT and the left stick, with repeat)
    }
    private TrainerSound trainerSound;
    // Update 1 (owner 2026-11-07): a closing Online request (Character, Places, Activities, Save Look ...) waits for its result; once the
    // library reports it done the side menu closes (SideMenuApply.poll) and the menu handler drains it (menuChanged).
    private boolean onlinePolling;
    private void watchOnline(SideMenuApply apply){
        if(onlinePolling || !apply.waiting() || sideMenuView==null)return;
        onlinePolling=true;
        sideMenuView.postDelayed(()->{
            onlinePolling=false;
            if(sideMenu==null)return;
            if(apply.poll(sideMenu)){Log.i("RecompQolTouch","SIDE_MENU online request done: menu closed");menuSource="online result";menuChanged();return;}
            watchOnline(apply);
        },100);
    }
    /** Update 1 (menus plan A3): the capture combo holds Select for the side menu while its setting is on (settings/menu.txt). */
    private SideMenuRows menuRows; // (the side menu's rows: its own settings)
    /** Update 1 (main): once a second, whether the library's local online session is up (state bit 24); when it changes, the hold rule is
     *  applied again (in a session Select goes to the game's own Interaction Menu). One JNI read of an atomic a second, UI thread only. */
    private boolean sessionUp;
    private final Runnable sessionWatch=new Runnable(){@Override public void run(){
        final int state=trainerState();
        // Update 1 Online mode: the controller's leave is done (bit 26): the game window closes and the launcher shows (the process ends
        // as for any finished window, in onStop). Read once a second, like the session bit; never set outside Online mode.
        if(MenuWords.onlineLeft(state)){Log.i(TAG,"ONLINE_LEAVE done: the game window closes, back to the launcher");finish();return;}
        final boolean s=MenuWords.localSession(state);
        if(s!=sessionUp){sessionUp=s;holdSelectSetting();Log.i(TAG,"SIDE_MENU local session "+(s?"up: hold Select goes to the game's Interaction Menu":"down: hold Select opens the side menu again"));}
        mainThread.postDelayed(this,1000);
    }};
    private void holdSelectSetting(){
        if(capture==null || sideMenu==null || menuRows==null)return;
        capture.passSelect(MenuWords.localSession(trainerState())); // (main: in a session Select reaches the game at once: ~350 ms, as GTA Online)
        // (Interaction Menu style: Select is never held back for the side menu: the library's own 350 ms hold opens its menu)
        capture.holdToOpen(MenuWords.holdOpensSideMenu(menuRows.holdSelect,menuRows.effectiveStyle(),trainerState()),()->{ // (+ main: a local session yields to the game)
            if(sideMenu==null || sideMenu.isOpen())return;
            Log.i("RecompQolTouch","SIDE_MENU opened by holding Select");sideMenu.openMenu();menuSource="hold Select";menuChanged();});
    }
    private void menuChanged(){
        if(sideMenu!=null && sideMenu.isOpen()!=padMenuWasOpen){padMenuWasOpen=sideMenu.isOpen();if(sideMenuPad!=null)sideMenuPad.reset();} // (Update 1 QoL)
        QolTouch.menuEvents(); // (the same handler as the touches': save, apply, log)
        if(sideMenuView!=null)sideMenuView.invalidate();
    }
    private void applyScreen(SurfaceView surface,int w,int h){
        if(w<1 || h<1)return;
        final ViewGroup.LayoutParams lp=surface.getLayoutParams();
        final float[] v;
        if(fillNow) {
            final int[] size=ScreenFill.layout(w,h,true),buffer=ScreenFill.buffer(w,h,fillNative);
            if(lp.width!=size[0] || lp.height!=size[1]){lp.width=size[0];lp.height=size[1];surface.setLayoutParams(lp);}
            surface.getHolder().setFixedSize(buffer[0],buffer[1]);
            v=ScreenFill.visible(w,h,true);
            Log.i("RecompQolTouch","QOL_SCREEN fill=1 screen="+w+"x"+h+" layout="+size[0]+"x"+size[1]+" buffer="+buffer[0]+"x"+buffer[1]+
                    " native="+(fillNative?1:0)+" visible="+ScreenFill.visibleText(v).trim());
        } else {
            if(lp.width!=ViewGroup.LayoutParams.MATCH_PARENT || lp.height!=ViewGroup.LayoutParams.MATCH_PARENT){
                lp.width=lp.height=ViewGroup.LayoutParams.MATCH_PARENT;surface.setLayoutParams(lp);}
            surface.getHolder().setSizeFromLayout();
            v=new float[]{1f,1f};
            Log.i("RecompQolTouch","QOL_SCREEN fill=0 screen="+w+"x"+h);
        }
        writeVisible(v);
    }
    private void writeVisible(float[] v){
        try{
            final java.io.File f=new java.io.File(getFilesDir(),ScreenFill.VISIBLE);
            final java.io.File dir=f.getParentFile();if(dir!=null && !dir.isDirectory())return; // (the library's own folder; never made here)
            try(FileOutputStream out=new FileOutputStream(f)){out.write(ScreenFill.visibleText(v).getBytes(java.nio.charset.StandardCharsets.US_ASCII));}
        }catch(Exception e){Log.w("RecompQolTouch","QOL_SCREEN visible file not written: "+e);}
    }

    private static String hex(byte[] digest){
        StringBuilder actual=new StringBuilder(64);
        for(byte part:digest)actual.append(String.format(java.util.Locale.ROOT,"%02x",part&255));
        return actual.toString();
    }

    private String verifyAndCheck(String expected,String requestId,String manifestHash) throws Exception {
        String directory=getFilesDir().getAbsolutePath();
        int raw=openLibrary(directory,frameMode);
        if(raw<0)return "REJECT_LIBRARY_"+(-raw);
        // Hash and dlopen use the SAME open descriptor, never a reopened path.
        // The known digest comes from the explicit local developer request;
        // this is an integrity check, not authentication against hostile same-UID code.
        ParcelFileDescriptor descriptor=ParcelFileDescriptor.adoptFd(raw);
        try(InputStream input=new ParcelFileDescriptor.AutoCloseInputStream(descriptor)){
            MessageDigest sha=MessageDigest.getInstance("SHA-256");
            byte[] buffer=new byte[65536];
            long bytes=0;
            for(int count;(count=input.read(buffer))!=-1;){
                if(count==0 || (bytes+=count)>512L*1024*1024)return "REJECT_SIZE";
                sha.update(buffer,0,count);
            }
            if(!hex(sha.digest()).equals(expected))return "REJECT_HASH";
            if(gameRequested){
                int manifestFd=openArguments(directory,requestId);
                if(manifestFd<0)return "REJECT_MANIFEST_FILE";
                byte[] manifest;
                try(InputStream source=new ParcelFileDescriptor.AutoCloseInputStream(ParcelFileDescriptor.adoptFd(manifestFd))){
                    ByteArrayOutputStream owned=new ByteArrayOutputStream();
                    for(int count;(count=source.read(buffer))!=-1;){
                        if(count==0 || owned.size()+count>65536)return "REJECT_MANIFEST_SIZE";
                        owned.write(buffer,0,count);
                    }
                    manifest=owned.toByteArray();
                }
                if(!hex(MessageDigest.getInstance("SHA-256").digest(manifest)).equals(manifestHash))return "REJECT_MANIFEST_HASH";
                runOnUiThread(()->show("RUNNING_GUEST"));
                byte[] result=runTrace(descriptor.getFd(),directory,requestId,manifest,frameMode);
                if(result==null || result.length<4){String refused=PlayMode.refusalWord(playModeRefusal());return refused!=null?refused:"REJECT_NATIVE_RUN";}
                int code=((result[0]&255)<<24)|((result[1]&255)<<16)|((result[2]&255)<<8)|(result[3]&255);
                if(code!=0)return "REJECT_RUN_SETUP_"+code;
                if(result.length==4)return "REJECT_EMPTY_PROTOCOL";
                // Only the bounded protocol is saved. No raw output goes to
                // logcat; the host must run its strict parser before claiming a result.
                String destination=directory+"/private-trace/run-"+requestId+"/trace-protocol.txt";
                FileDescriptor file=Os.open(destination,OsConstants.O_WRONLY|OsConstants.O_CREAT|
                        OsConstants.O_EXCL|OsConstants.O_NOFOLLOW|OsConstants.O_CLOEXEC,0600);
                try(FileOutputStream saved=new FileOutputStream(file)){saved.write(result,4,result.length-4);}
                return "TRACE_RETURNED_PROTOCOL_SAVED";
            }
            int code=checkAbi(descriptor.getFd(),directory);
            return code==0?"ABI_PASS":"REJECT_ABI_"+code;
        }
    }

    private void show(String result){
        Log.i(TAG,result+" pid="+Process.myPid()+" uid="+Process.myUid());
        // A refusal or a stop of a request the launcher made and this window accepted is left for the launcher to read.
        GameWindowStatus.report(getFilesDir(),acceptedLauncherRequest,result);
        String text=GameWindowStatus.label(result);
        // Nothing runs that could want the button once a start was refused or the game has stopped: B then leaves.
        leaveWithB=(plainWindow||launcherStart) && text!=null;
        if(plainWindow) {
            // Nothing over the picture while the game is checked, waited for or running; plain words otherwise.
            status.setVisibility(text==null?View.GONE:View.VISIBLE);
            if(text!=null){status.setTextSize(18);status.setPadding(48,32,48,32);status.setText(text);}
            return;
        }
        if(liveRequested) {
            String state="RUNNING_GUEST".equals(result)?"Running native game code":
                    "TRACE_RETURNED_PROTOCOL_SAVED".equals(result)?"STOPPED — last frame only; protocol saved":
                    result.replace('_',' ');
            status.setText("Development build · "+state+"\nGameplay unverified · Android Back exits");
            return;
        }
        status.setText((liveRequested?"Experimental ordered native swap":
                (previewRequested?"STOPPED image consumer — not live game presentation":"Private native-library diagnostic"))+"\n\n"+result+
                (gameRequested?"\n\nGuarded game startup diagnostic.\nProtocol requires host verification.\nTitle screen and gameplay not verified.":
                    "\n\nSeparate debug process; bounded ABI check only.\nNo game execution, native swap or gameplay.")+
                "\nBack exits this process.");
    }

    // Update 1 haptics: the motor is silent while the window is not in front (and through BUG 02's background grace, which follows
    // onStop); back in front, a rumble the game still holds returns.
    @Override protected void onPause(){super.onPause();if(haptics!=null)haptics.pause();}
    @Override protected void onResume(){super.onResume();if(haptics!=null)haptics.resume();}
    @Override protected void onStart(){
        super.onStart();
        if(music!=null)music.foreground(true); // (Update 1 P6)
        if(grace.started())Log.i(TAG,"BACKGROUND_GRACE back after_ms="+(android.os.SystemClock.uptimeMillis()-stoppedAt));
    }
    @Override protected void onStop(){
        super.onStop();
        if(music!=null)music.foreground(false); // (Update 1 P6: the music pauses in the background)
        // BUG 02: a brief app switch no longer ends the game; it ends BackgroundGrace.GRACE_MS later unless the window is back.
        // A finishing window ends at once, as before (no reload offered). Process death still bounds native/TLS lifetime.
        if(!privateProcess)return;
        stoppedAt=android.os.SystemClock.uptimeMillis();
        Log.i(TAG,"BACKGROUND_GRACE stop finishing="+(isFinishing()?1:0)+" grace_ms="+BackgroundGrace.GRACE_MS);
        // Update 1 (Mods page): the library's per-mod status of this start, for the launcher's Mods page (Loaded / Failed + reason).
        // Once per stop, never per frame; at most 64 KiB (ModsLibrary.saveStatus never throws).
        if(acceptedLauncherRequest!=null)ModsLibrary.saveStatus(getFilesDir(),acceptedLauncherRequest,modsStatus());
        grace.stopped(isFinishing());
    }
}
