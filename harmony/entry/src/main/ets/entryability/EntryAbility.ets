import { UIAbility, Want, AbilityConstant } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { webview } from '@kit.ArkWeb';
import { pushService } from '@kit.PushKit';
import { parsePushTarget } from '../model/PushRoute';
import { hilog } from '@kit.PerformanceAnalysisKit';

export default class EntryAbility extends UIAbility {
  onCreate(want: Want, _launchParam: AbilityConstant.LaunchParam): void {
    this.captureNotification(want);
    try { pushService.on('tokenUpdate', this, () => this.onForeground()); } catch (_) {}
  }

  onDestroy(): void {
    try { pushService.off('tokenUpdate'); } catch (_) {}
  }

  onNewWant(want: Want, _launchParam: AbilityConstant.LaunchParam): void {
    this.captureNotification(want);
  }

  private captureNotification(want: Want): void {
    const target = parsePushTarget(want.uri ?? '');
    if (target) AppStorage.setOrCreate('pushTarget', JSON.stringify(target));
  }

  onForeground(): void {
    AppStorage.setOrCreate('pushRefresh', (AppStorage.get<number>('pushRefresh') ?? 0) + 1);
  }

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
