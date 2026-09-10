using System;
using System.Collections;
using UnityEngine;
#if UNITY_PURCHASING
using UnityEngine.Purchasing;
#endif
#if LEVELPLAY_DEPENDENCIES_INSTALLED
using Unity.Services.LevelPlay;
#endif

namespace GameFactory.Core
{
    /// <summary>
    /// One gateway for rewarded ads and store purchases. Editor/development builds use a
    /// deterministic mock when dashboard credentials are blank; release builds never grant
    /// a reward or purchase without a provider callback.
    /// </summary>
    public class MonetizationService : MonoBehaviour
#if UNITY_PURCHASING
        , IStoreListener
#endif
    {
        public const string CoinPackProductId = "com.gamefactory.game01.coins500";
        public const string RemoveAdsProductId = "com.gamefactory.game01.removeads";
        public const string RemoveAdsKey = "purchase.remove_ads";

        public static MonetizationService Instance { get; private set; }

        [Header("LevelPlay dashboard credentials")]
        [SerializeField] private string levelPlayAppKey = "";
        [SerializeField] private string rewardedAdUnitId = "";
        [SerializeField] private string interstitialAdUnitId = "";

        public bool IsRewardedReady
        {
            get
            {
                if (UseLocalMock) return true;
#if LEVELPLAY_DEPENDENCIES_INSTALLED
                return rewardedAd != null && rewardedAd.IsAdReady();
#else
                return false;
#endif
            }
        }

        public bool IsStoreReady
        {
            get
            {
                if (UseLocalMock) return true;
#if UNITY_PURCHASING
                return storeController != null;
#else
                return false;
#endif
            }
        }

        public event Action AvailabilityChanged;
        public event Action<string> PurchaseSucceeded;

        private Action<bool> pendingReward;
        private bool rewardGranted;
        private bool UseLocalMock => (Application.isEditor || Debug.isDebugBuild)
                                     && string.IsNullOrWhiteSpace(levelPlayAppKey);

#if LEVELPLAY_DEPENDENCIES_INSTALLED
        private LevelPlayRewardedAd rewardedAd;
        private LevelPlayInterstitialAd interstitialAd;
#endif
#if UNITY_PURCHASING
        private IStoreController storeController;
#endif

        private void Awake()
        {
            if (Instance != null && Instance != this)
            {
                Destroy(gameObject);
                return;
            }
            Instance = this;
        }

        private void Start()
        {
            InitializePurchasing();
            if (SettingsSystem.AdsConsentSet) InitializeAds();
            AvailabilityChanged?.Invoke();
        }

        public void ApplyAdConsent(bool personalized)
        {
            SettingsSystem.PersonalizedAds = personalized;
#if LEVELPLAY_DEPENDENCIES_INSTALLED
            LevelPlayPrivacySettings.SetGDPRConsent(personalized);
#endif
            InitializeAds();
        }

        public void Configure(string appKey, string rewardedId, string interstitialId)
        {
            levelPlayAppKey = appKey ?? string.Empty;
            rewardedAdUnitId = rewardedId ?? string.Empty;
            interstitialAdUnitId = interstitialId ?? string.Empty;
        }

        public void ShowInterstitialIfDue(string gameId, int completedRuns)
        {
            if (completedRuns <= 0 || completedRuns % 3 != 0) return;
            if (SaveSystem.GetInt(gameId, RemoveAdsKey) != 0 || UseLocalMock) return;
#if LEVELPLAY_DEPENDENCIES_INSTALLED
            if (interstitialAd != null && interstitialAd.IsAdReady())
                interstitialAd.ShowAd("game_over");
#endif
        }

        public void ShowRewarded(Action<bool> completion)
        {
            if (pendingReward != null || !IsRewardedReady)
            {
                completion?.Invoke(false);
                return;
            }

            pendingReward = completion;
            rewardGranted = false;
            if (UseLocalMock)
            {
                StartCoroutine(CompleteMockReward());
                return;
            }

#if LEVELPLAY_DEPENDENCIES_INSTALLED
            rewardedAd.ShowAd("double_run_coins");
#else
            FinishReward(false);
#endif
        }

        public void PurchaseCoinPack() => Purchase(CoinPackProductId);
        public void PurchaseRemoveAds() => Purchase(RemoveAdsProductId);

        private IEnumerator CompleteMockReward()
        {
            yield return null;
            FinishReward(true);
        }

        private void InitializeAds()
        {
            if (UseLocalMock || string.IsNullOrWhiteSpace(levelPlayAppKey))
            {
                AvailabilityChanged?.Invoke();
                return;
            }

#if LEVELPLAY_DEPENDENCIES_INSTALLED
            LevelPlay.OnInitSuccess -= HandleAdsInitialized;
            LevelPlay.OnInitFailed -= HandleAdsInitializationFailed;
            LevelPlay.OnInitSuccess += HandleAdsInitialized;
            LevelPlay.OnInitFailed += HandleAdsInitializationFailed;
            LevelPlayPrivacySettings.SetGDPRConsent(SettingsSystem.PersonalizedAds);
            LevelPlay.Init(levelPlayAppKey);
#endif
        }

#if LEVELPLAY_DEPENDENCIES_INSTALLED
        private void HandleAdsInitialized(LevelPlayConfiguration configuration)
        {
            if (string.IsNullOrWhiteSpace(rewardedAdUnitId)) return;
            rewardedAd = new LevelPlayRewardedAd(rewardedAdUnitId);
            rewardedAd.OnAdLoaded += _ => AvailabilityChanged?.Invoke();
            rewardedAd.OnAdLoadFailed += _ => AvailabilityChanged?.Invoke();
            rewardedAd.OnAdRewarded += HandleAdRewarded;
            rewardedAd.OnAdClosed += HandleAdClosed;
            rewardedAd.OnAdDisplayFailed += (_, __) => FinishReward(false);
            rewardedAd.LoadAd();

            if (!string.IsNullOrWhiteSpace(interstitialAdUnitId))
            {
                interstitialAd = new LevelPlayInterstitialAd(interstitialAdUnitId);
                interstitialAd.OnAdClosed += _ => interstitialAd.LoadAd();
                interstitialAd.OnAdDisplayFailed += (_, __) => interstitialAd.LoadAd();
                interstitialAd.LoadAd();
            }
        }

        private void HandleAdsInitializationFailed(LevelPlayInitError error)
        {
            Debug.LogWarning($"[Monetization] LevelPlay init failed: {error.ErrorMessage}");
            AvailabilityChanged?.Invoke();
        }

        private void HandleAdRewarded(LevelPlayAdInfo info, LevelPlayReward reward)
        {
            rewardGranted = true;
            FinishReward(true);
        }

        private void HandleAdClosed(LevelPlayAdInfo info)
        {
            if (!rewardGranted) FinishReward(false);
            rewardedAd?.LoadAd();
        }
#endif

        private void FinishReward(bool success)
        {
            Action<bool> completion = pendingReward;
            pendingReward = null;
            completion?.Invoke(success);
            AvailabilityChanged?.Invoke();
        }

        private void Purchase(string productId)
        {
            if (UseLocalMock)
            {
                GrantPurchase(productId);
                return;
            }
#if UNITY_PURCHASING
            Product product = storeController?.products.WithID(productId);
            if (product != null && product.availableToPurchase)
                storeController.InitiatePurchase(product);
#endif
        }

        private void InitializePurchasing()
        {
#if UNITY_PURCHASING
            if (storeController != null) return;
            StandardPurchasingModule module = StandardPurchasingModule.Instance();
            ConfigurationBuilder builder = ConfigurationBuilder.Instance(module);
            builder.AddProduct(CoinPackProductId, ProductType.Consumable);
            builder.AddProduct(RemoveAdsProductId, ProductType.NonConsumable);
            UnityPurchasing.Initialize(this, builder);
#endif
        }

        private void GrantPurchase(string productId)
        {
            string gameId = GameManager.Instance != null ? GameManager.Instance.GameId : "game01";
            if (productId == CoinPackProductId)
                SaveSystem.AddInt(gameId, ShopKeys.Currency, 500);
            else if (productId == RemoveAdsProductId)
                SaveSystem.SaveInt(gameId, RemoveAdsKey, 1);
            else
                return;
            PurchaseSucceeded?.Invoke(productId);
            AvailabilityChanged?.Invoke();
        }

#if UNITY_PURCHASING
        public void OnInitialized(IStoreController controller, IExtensionProvider extensions)
        {
            storeController = controller;
            Product removeAds = controller.products.WithID(RemoveAdsProductId);
            if (removeAds != null && removeAds.hasReceipt) GrantPurchase(RemoveAdsProductId);
            AvailabilityChanged?.Invoke();
        }

        public void OnInitializeFailed(InitializationFailureReason error) => AvailabilityChanged?.Invoke();
        public void OnInitializeFailed(InitializationFailureReason error, string message) => AvailabilityChanged?.Invoke();

        public PurchaseProcessingResult ProcessPurchase(PurchaseEventArgs args)
        {
            GrantPurchase(args.purchasedProduct.definition.id);
            return PurchaseProcessingResult.Complete;
        }

        public void OnPurchaseFailed(Product product, PurchaseFailureReason failureReason)
        {
            AvailabilityChanged?.Invoke();
        }
#endif

        private void OnDestroy()
        {
#if LEVELPLAY_DEPENDENCIES_INSTALLED
            LevelPlay.OnInitSuccess -= HandleAdsInitialized;
            LevelPlay.OnInitFailed -= HandleAdsInitializationFailed;
            rewardedAd?.Dispose();
            interstitialAd?.Dispose();
#endif
            if (Instance == this) Instance = null;
        }
    }
}
