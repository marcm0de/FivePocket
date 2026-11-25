package dev.recompbootstrap.smoke;

/**
 * Thor edition (thor-radio v826): the one-screen edition's v21 effects without its touch-control and screen effects.
 * side-menu (SPEC 5.3): what the side menu's events do. A setting's change is saved at once through the launcher's own store (the same
 * files a Save on the Settings tab writes; the game polls the live ones, and the app applies its own: the touch controls, the screen);
 * the trainer's rows become recomp_trainer_v1 requests (trainer.h Action). Pure Java: tests/SideMenuTest.java runs it with a recording
 * Effects.
 */
final class SideMenuApply {
    /** What the game activity does for the menu. */
    interface Effects {
        boolean save(Settings settings);               // SettingsStore.save (true: written); the rows show the saved state
        void trainerEnabled(boolean on);               // settings/trainer.txt "trainer=on|off", then a Sync request
        int trainer(int action,int a0,int a1);         // recomp_trainer_v1; < 0: refused (off, arguments, queue full)
        default void hapticsChanged(int mode){}        // Update 1 haptics: Haptics.setMode (the saved Vibration value)
        default void menuWords(int holdSelect){}      // Update 1: settings/menu.txt (MenuWords: other words kept), applied at once
        default void quickMenuStyle(int style){}      // Update 1: settings/quick-menu.txt, then recomp_imenu_v1(3|4) once (applied at once)
        /** 2026-11-09: a short notice over the game ("+$100,000" after Add Cash), with the confirm sound. (Owner Thor line qol25e; restored
         *  2026-11-24: the owner variant check found it missing in the next update.) */
        default void notice(String text){}
        /** Update 1 (main): open the game's own Interaction Menu (recomp_imenu_v1(5)): >= 0 accepted, -3 no local session, other < 0 not in
         *  this build (old libraries: -2 for op 5, -100 without the export). */
        default int gameInteractionMenu(){return -2;}
        /** Update 1 Diagnostics: build the report off the UI thread, put it on the clipboard (no share sheet: leaving the game window
         *  would end the game), then done.accept(the sentence) on the UI thread. */
        default void copyDiagnostics(java.util.function.Consumer<String> done){done.accept(Diagnostics.failed("not in this window"));}
    }
    // trainer.h Action
    static final int SYNC=0,TOGGLE=1,WANTED=2,HEAL=3,GIVE_WEAPONS=4,SPAWN_VEHICLE=5,FIX_VEHICLE=6,TELEPORT_WAYPOINT=7,TELEPORT_LANDMARK=8,
            SET_TIME=9,SET_WEATHER=10,CLEAR_WEATHER=11,
            SWITCH_CHARACTER=13; // (Update 1 P7, trainer.h SwitchCharacter: a0 = the character, a1 = 0 the game's player-ped change; Sandbox only)

    private final SideMenuRows rows;private final Effects fx;
    SideMenuApply(SideMenuRows rows,Effects fx){this.rows=rows;this.fx=fx;}

    void apply(SideMenu.Event e){
        if(e.type==SideMenu.Event.Type.CLOSED)rows.close.menuClosed(); // (Update 1: a request waited for is no longer)
        if(e.type==SideMenu.Event.Type.HOLD){pin(e.id);return;} // (Update 1 QoL: hold A / touch and hold pins a trainer item)
        if(e.type==SideMenu.Event.Type.ACTION || e.type==SideMenu.Event.Type.CHANGE){rows.pinNote=null;rows.imenuNote=null;}
        if(e.type==SideMenu.Event.Type.ACTION && SideMenuRows.ID_GAME_IMENU.equals(e.id)){ // (Update 1: the game's own menu; ours closes)
            final int r=fx.gameInteractionMenu();
            if(r>=0)closeNow=true;else rows.imenuNote=r==-3?SideMenuRows.TEXT_IMENU_NO_SESSION:SideMenuRows.TEXT_IMENU_NOT_IN_BUILD;
            return;}
        current=e;
        if(e.id!=null && e.id.startsWith("on:")){online(e);return;} // (Update 1: the Online tab)
        if(e.type==SideMenu.Event.Type.CHANGE)change(e.id,e.value);
        else if(e.type==SideMenu.Event.Type.ACTION)action(e.id);
    }
    /** Update 1, the Online tab: its requests; what the library returned is kept (OnlineMenu.sent: a closing row waits for its result,
     *  a refusal is said). The first refused request stops the event's others (an overlay's opacity after a refused index). */
    private void online(SideMenu.Event e){
        for(int[] r:rows.online.requests(e)){
            final int seq=fx.trainer(r[0],r[1],r[2]);
            rows.online.sent(e.id,seq);rows.close.sent(e.id,e.type,seq,clock.getAsLong());
            if(seq<0)return;
        }
    }
    /** Called while a closing Online request or a switch waits (waiting()), about ten times a second: once the library reports the
     *  closing request done, the menu closes (its CLOSED event is the host's to drain); once a switch to an online character is done,
     *  the saved look is asked for. True when it closed the menu. */
    private boolean closeNow; // (Update 1: the game's Interaction Menu was opened: the side menu closes at the next poll)
    boolean poll(SideMenu menu){
        if(closeNow){closeNow=false;if(menu!=null && menu.isOpen()){menu.close();return true;}}
        if(rows.trainer==null)return false;
        final String raw=rows.trainer.status();
        final int[] then=rows.online.afterSwitch(raw,rows.trainer.state()); // (the saved look after a switch to an online character: OnlineMenu.afterSwitch)
        final String cash=rows.online.cashDone(raw); // (2026-11-09: Add Cash done: "+$N")
        if(cash!=null)fx.notice(cash);
        boolean closed=false;
        if(rows.close.waiting()){
            if(menu==null || !menu.isOpen())rows.close.menuClosed();
            else if(rows.close.poll(raw,clock.getAsLong(),rows.trainer.state())){menu.close();closed=true;}
        }
        if(then!=null)fx.trainer(then[0],then[1],then[2]);
        return closed;
    }
    boolean waiting(){return rows.close.waiting() || rows.online.switching();}
    /** Update 1 QoL: the event just applied came from a menu that stays open after an action (the Thor's lower screen). */
    void stayOpen(){rows.close.menuClosed();}
    /** The time in ms the settle delay counts in (ActionClose.SETTLE_MS); the host's uptime by default, a fake in the tests. */
    java.util.function.LongSupplier clock=()->System.nanoTime()/1_000_000L;
    private SideMenu.Event current; // (the event whose requests trainer() sends: the close rule is read from its row)
    /** A trainer request of the current event's row, reported to the close rule (ActionClose: closes once done, or stays). */
    private int trainer(int action,int a0,int a1){
        final int r=fx.trainer(action,a0,a1);
        if(current!=null)rows.close.sent(current.id,current.type,r,clock.getAsLong());
        return r;
    }
    /** Update 1 QoL: pin or unpin a trainer item (Favourites); its line on the trainer pages says which. */
    private void pin(String id){
        if(!Favourites.pinnable(id))return;
        rows.pinNote=rows.favourites.toggle(id)?Favourites.TEXT_PINNED:Favourites.TEXT_UNPINNED;
    }
    private void change(String id,int index){
        switch(id){
            case "tr:on":fx.trainerEnabled(index==1);return;
            case SideMenuRows.ID_HOLD_SELECT:rows.holdSelect=index==1?MenuWords.HOLD_ON:MenuWords.HOLD_OFF;fx.menuWords(rows.holdSelect);return;
            case SideMenuRows.ID_MENU_STYLE:{
                if(!rows.capabilities.interactionMenu())return; // (no row without the export)
                final int style=index==1?MenuWords.STYLE_INTERACTION:MenuWords.STYLE_FIVEPOCKET;
                if(style==rows.menuStyle)return;
                rows.menuStyle=style;fx.quickMenuStyle(style);return;}
            case "tr:wanted":rows.wanted=index;trainer(WANTED,index,0);return;
            case "tr:time":rows.hour=index;trainer(SET_TIME,index,0);return; // (menu-redesign: Time Of Day as a value row; the hour, minute 0)
            default:break;
        }
        if(id.startsWith("tr:t")){trainer(TOGGLE,Integer.parseInt(id.substring(4)),index==1?1:0);return;}
        final int value=rows.settingValue(id,index);
        if(value<0)return; // (not a setting row of the current build)
        final Settings next=rows.settings.with(id,value);
        if(!fx.save(next))return; // (not written: the rows keep the saved state)
        rows.settings=next;
        if(id.equals(SettingsSchema.HAPTICS))fx.hapticsChanged(value);
        if(rows.music!=null && SideMenuRows.MUSIC.contains(id))rows.music.settings(next); // (Update 1 P6: the player takes them at once)
    }
    private void action(String id){
        if(id.startsWith("mm:")){if(rows.music!=null)rows.music.action(id);return;} // (Update 1 P6: My Music's Next / Previous / Look For New Music)
        if(id.equals(SideMenuRows.ID_DIAGNOSTICS)){rows.diagNote="Copying diagnostics\u2026";fx.copyDiagnostics(r->rows.diagNote=r);return;} // (Update 1)
        switch(id){
            case "tr:heal":trainer(HEAL,0,0);return;
            case "tr:weapons":trainer(GIVE_WEAPONS,0,0);return;
            case "tr:fix":trainer(FIX_VEHICLE,0,0);return;
            case "tr:waypoint":trainer(TELEPORT_WAYPOINT,0,0);return;
            case "tr:clearwx":trainer(CLEAR_WEATHER,0,0);return;
            default:break;
        }
        final int colon=id.indexOf(':');
        if(colon<0)return;
        final int i;try{i=Integer.parseInt(id.substring(colon+1));}catch(NumberFormatException no){return;}
        switch(id.substring(0,colon)){
            case "veh":trainer(SPAWN_VEHICLE,i,1);break; // (a1 = 1: a0 is an index into the trainer's vehicle list)
            case "lm":trainer(TELEPORT_LANDMARK,i,0);break;
            case "wx":trainer(SET_WEATHER,i,0);break;
            case "chr":trainer(SWITCH_CHARACTER,i,0);break; // (Update 1 P7: a1 = 0, never the comparison method)
            default:break;
        }
    }
}
