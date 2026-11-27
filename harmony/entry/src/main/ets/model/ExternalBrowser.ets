import { common } from '@kit.AbilityKit';
import { httpsOrigin } from './UrlPolicy';

export class ExternalBrowser {
  private pending: boolean = false;
  private lastUrl: string = '';
  private lastAttempt: number = 0;

  async open(value: string, context: common.UIAbilityContext): Promise<void> {
    if (httpsOrigin(value) === '') throw new Error('Unsupported browser URL');
    // ArkWeb can report the same navigation through both interception callbacks.
    // Keep dispatch single-flight and suppress their immediate duplicate event.
    const now = Date.now();
    if (this.pending || (this.lastUrl === value && now - this.lastAttempt < 1000)) return;
    this.pending = true;
    this.lastUrl = value;
    this.lastAttempt = now;
    try {
      // Resolve the user's browser through the OS, without a vendor bundle ID,
      // copied cookies, JavaScript bridges, or a second external-site WebView.
      await context.startAbility({
        action: 'ohos.want.action.viewData',
        entities: ['entity.system.browsable'],
        uri: value
      });
    } finally {
      this.pending = false;
    }
  }
}
