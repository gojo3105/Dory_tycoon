# Meta game and monetization

## Player loop

- Ten stages unlock in order. Each stage has a target distance, per-stage best score, three stars, and a small speed increase.
- Runs award XP from distance and collected coins. XP persists and raises the player level.
- Three UTC daily missions track cumulative distance, collected jellies, and the highest combo. Completed missions require an explicit reward claim.
- A seven-day attendance streak awards 30-90 coins once per UTC day.
- The first launch shows a three-page Korean tutorial before the title screen can be used.

All profile data uses `SaveSystem` and is namespaced by GameSpec id. Global audio, vibration, and ad privacy choices use `SettingsSystem`.

## Store products

Configure these product IDs in Google Play Console with the same types:

| Product ID | Type | Fulfilment |
|---|---|---|
| `com.gamefactory.game01.coins500` | Consumable | Adds 500 shop coins |
| `com.gamefactory.game01.removeads` | Non-consumable | Suppresses automatic game-over interstitials |

Unity IAP 4.14 is installed. The Unity Editor and development builds use the local mock only when the LevelPlay app key is blank. Release builds never grant purchases or ad rewards without a provider callback.

## LevelPlay

LevelPlay 9.5 is installed. Scene generation reads credentials from build-time environment variables so secrets are not committed:

- `LEVELPLAY_ANDROID_APP_KEY`
- `LEVELPLAY_REWARDED_AD_UNIT_ID`
- `LEVELPLAY_INTERSTITIAL_AD_UNIT_ID`

`Assets/Plugins/Android/mainTemplate.gradle` adds the matching official Maven artifact, `com.unity3d.ads-mediation:mediation-sdk:9.5.0`, required by the Unity package's Android bridge.
The same template pins Kotlin 1.8.22 and excludes its obsolete split jdk7/jdk8 artifacts to prevent duplicate Android classes with Unity IAP.

Rewarded ads offer a second copy of the run's collected coins. Interstitials appear after every third completed run and are disabled by the remove-ads purchase. The SDK is initialized only after the tutorial or settings flow records an ad privacy choice.

Before store release, create the app/ad units in the LevelPlay dashboard, configure the two products in Google Play Console, add a production CMP where required, and validate purchases and ads on an internal-test device account.
