# Receive task notifications on your phone

[中文](mobile-notifications.md) · [Home](../README_en.md) · [Server configuration](configuration_en.md)

cc-remote uses browser **Web Push** to alert you when a task finishes, fails, is interrupted or needs an answer. Delivery requires a configured server, a phone subscription and OS notification permission. Permission on your computer does not enable notifications on your phone.

## Supported paths

| Device | How to use it | Requirements |
|---|---|---|
| iPhone / iPad | Add the site to the home screen with Safari, then launch its icon | iOS/iPadOS 16.4 or later, HTTPS and notification permission |
| Android | A browser supporting Web Push, such as Firefox or Chrome | HTTPS, browser and OS permission, reachable push services |
| In-app browsers such as WeChat | Open the site in a system browser | Embedded browsers do not guarantee full Web Push support |

Notifications appear through the OS notification center or lock screen. Banners, sound and background behavior follow system settings. Browser push delivery is also affected by battery policy and network access.

## Step 1: enable server-side Push

The operator must set these three values in Relay's external configuration and activate them through the existing deployment procedure:

| Setting | Purpose |
|---|---|
| `PUSH_VAPID_PUBLIC_KEY` | VAPID public key used by the browser subscription |
| `PUSH_VAPID_PRIVATE_KEY` | Absolute path to its matching private key, readable by the Relay user |
| `PUSH_VAPID_SUBJECT` | Contact URI using a valid `mailto:` or `https://` address |

Keep the private key outside source and release directories and preserve it across updates. The authenticated `/api/push-config` response reports `enabled: true` when the server is configured. This does not establish that a phone subscribed or received anything. See [configuration](configuration_en.md) for full parameters.

Regular users do not need their own keys; use the HTTPS site provided by the operator.

## Step 2: enable notifications on the phone

### iPhone / iPad

1. Open your cc-remote HTTPS address in **Safari**.
2. Select **Share → Add to Home Screen**.
3. Launch cc-remote from its new home-screen icon and sign in.
4. Open **⋮ → Desktop notifications** in the app. The setting also applies to phones.
5. Select generic or session-labelled notifications and allow the OS permission request.

An ordinary Safari tab is not a substitute for the home-screen Web App's push setup. If no permission prompt appears, check the OS version, how the app was launched and its notification permission in Settings.

Reference: [WebKit: Web Push for iOS/iPadOS Web Apps](https://webkit.org/blog/13878/web-push-for-web-apps-on-ios-and-ipados/).

### Android

1. Open your cc-remote HTTPS address in a browser supporting Web Push and sign in.
2. Open **⋮ → Desktop notifications**, select a notification mode and allow notifications.
3. Confirm that Android settings allow notifications for the browser and site.
4. For delayed delivery, check browser background and battery restrictions. You can also install the site using the browser's home-screen option.

Browsers can use different push networks. Chrome generally uses Google FCM; Firefox uses Mozilla's push service. Relay and the phone must both reach the chosen service. If a channel is unreachable, try a browser whose push network is reachable.

## Step 3: choose a notification mode

| Mode | Content | Clicking the notification |
|---|---|---|
| Off | No system notifications | — |
| Generic | Task status or a request for an answer, without a session name | Opens the site home page |
| Show session name | A bounded session name, without full conversations or tool output | Opens the matching session after device and session authorization |

Choose the session-name mode for direct task navigation. This also puts the session name in phone notifications; choose according to your privacy preference.

## Step 4: verify actual delivery

1. Use **Send test notification** and check whether the phone shows a notification. This tests submitting a display request on the current device.
2. Confirm that settings report **background Push connected**, rather than page-only alerts.
3. Put the app in the background, wait for an existing task to finish or ask a question, and check for a system notification.
4. With session-labelled notifications, click the notification and verify the target device and session.

The test button's accepted-display message means only that the browser accepted the request. It does not prove delivery from Relay or that the OS displayed a banner.

## Troubleshooting

**Notifications arrive on the computer but not the phone**

Each browser stores its own mode, permission and subscription. Enable them separately on the phone. Recheck after changing addresses, switching HTTP to HTTPS or clearing site data.

**Settings still report page-only alerts**

Confirm server-side Push is enabled, the browser supports it and permission is granted. Refresh and select a notification mode again. Page-only status means remote subscription is not established and cannot provide closed-page alerts.

**Push is connected, but the lock screen shows no banner**

Check OS permissions, focus/do-not-disturb settings, background restrictions and network access. Sign-in may also have expired; signing in and enabling alerts again can renew the subscription. Subscription acceptance does not guarantee every notification arrives.

**A notification opens the home page instead of the task**

Generic notifications deliberately carry no session route. Choose the session-name mode for subsequent notifications to support session navigation. Navigation opens the conversation; it does not guarantee scrolling to a particular historical message.

**Can delivery be guaranteed like a native messenger after closing or force-stopping the browser?**

No equivalent guarantee is made. Web Push supports background notifications under the browser and OS rules, subject to network access and subscription lifetime. Force-stopping, disabling background operation or using an incompatible browser can prevent delivery.
