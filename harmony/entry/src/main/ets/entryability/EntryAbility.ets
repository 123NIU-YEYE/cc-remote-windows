import { UIAbility } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { webview } from '@kit.ArkWeb';
import { hilog } from '@kit.PerformanceAnalysisKit';

export default class EntryAbility extends UIAbility {
  onWindowStageCreate(stage: window.WindowStage): void {
    // Keep the Web viewport between the system bars. The page owns its internal
    // header and drawer; no second native toolbar or gesture recognizer wraps it.
    try {
      stage.getMainWindowSync().setWindowLayoutFullScreen(false).catch(() => {});
    } catch (_) {
      hilog.warn(0x0000, 'cc-remote', 'Could not configure system-bar layout');
    }
    stage.loadContent('pages/Index').catch(() => {
      hilog.error(0x0000, 'cc-remote', 'Could not load the main page');
    });
  }

  onBackground(): void {
    webview.WebCookieManager.saveCookieAsync().catch(() => {});
  }

  // Foregrounding never reloads Web, changes sessions or submits model input.
}
