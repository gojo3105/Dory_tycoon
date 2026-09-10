using UnityEngine;

namespace GameFactory.Core
{
    /// <summary>Global (not per-game) player preferences shared across every generated game.</summary>
    public static class SettingsSystem
    {
        private const string SoundEnabledKey = "settings.sound_enabled";
        private const string VibrationEnabledKey = "settings.vibration_enabled";
        private const string AdsConsentSetKey = "settings.ads_consent_set";
        private const string PersonalizedAdsKey = "settings.personalized_ads";

        public static bool SoundEnabled
        {
            get => PlayerPrefs.GetInt(SoundEnabledKey, 1) == 1;
            set
            {
                PlayerPrefs.SetInt(SoundEnabledKey, value ? 1 : 0);
                PlayerPrefs.Save();
            }
        }

        public static bool VibrationEnabled
        {
            get => PlayerPrefs.GetInt(VibrationEnabledKey, 1) == 1;
            set
            {
                PlayerPrefs.SetInt(VibrationEnabledKey, value ? 1 : 0);
                PlayerPrefs.Save();
            }
        }

        public static bool AdsConsentSet
        {
            get => PlayerPrefs.GetInt(AdsConsentSetKey, 0) == 1;
            set
            {
                PlayerPrefs.SetInt(AdsConsentSetKey, value ? 1 : 0);
                PlayerPrefs.Save();
            }
        }

        public static bool PersonalizedAds
        {
            get => PlayerPrefs.GetInt(PersonalizedAdsKey, 0) == 1;
            set
            {
                PlayerPrefs.SetInt(PersonalizedAdsKey, value ? 1 : 0);
                AdsConsentSet = true;
                PlayerPrefs.Save();
            }
        }
    }
}
