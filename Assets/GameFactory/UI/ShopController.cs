using System.Globalization;
using GameFactory.Core;
using UnityEngine;
using UnityEngine.UI;

namespace GameFactory.UI
{
    /// <summary>
    /// Drives the Shop panel: shows the player's persistent currency
    /// balance and lets them buy/equip the two MVP items (coin magnet,
    /// red skin). Reads/writes SaveSystem directly via ShopKeys - the
    /// items themselves (CoinMagnet, MainCharacterSkin) read the same keys
    /// independently at their own Start, so no runtime wiring is needed
    /// beyond the purchase happening before the next run starts.
    /// </summary>
    public class ShopController : MonoBehaviour
    {
        [SerializeField] private GameObject shopPanel;
        [SerializeField] private Text currencyText;

        /// <summary>
        /// Every button that opens the shop. There are two - one on the title
        /// screen and one on the game-over card - because the shop was
        /// previously reachable only after dying, so a player who had just
        /// earned coins had to lose a run to spend them.
        /// </summary>
        [SerializeField] private Button[] openButtons;

        [SerializeField] private Button coinMagnetButton;
        [SerializeField] private Text coinMagnetButtonLabel;
        [SerializeField] private Button redSkinButton;
        [SerializeField] private Text redSkinButtonLabel;
        [SerializeField] private Button coinPackButton;
        [SerializeField] private Text coinPackButtonLabel;
        [SerializeField] private Button removeAdsButton;
        [SerializeField] private Text removeAdsButtonLabel;
        [SerializeField] private Button closeButton;

        private string gameId;
        private PanelTransition shopTransition;

        /// <summary>Wires structural references. Called at edit time by SceneGenerator.</summary>
        public void SetReferences(GameObject panel, Text currency, Button[] open, Button coinMagnet,
            Text coinMagnetLabel, Button redSkin, Text redSkinLabel, Button coinPack,
            Text coinPackLabel, Button removeAds, Text removeAdsLabel, Button close)
        {
            shopPanel = panel;
            currencyText = currency;
            openButtons = open;
            coinMagnetButton = coinMagnet;
            coinMagnetButtonLabel = coinMagnetLabel;
            redSkinButton = redSkin;
            redSkinButtonLabel = redSkinLabel;
            coinPackButton = coinPack;
            coinPackButtonLabel = coinPackLabel;
            removeAdsButton = removeAds;
            removeAdsButtonLabel = removeAdsLabel;
            closeButton = close;
        }

        /// <summary>
        /// Button clicks are hooked up here, at runtime, and not by
        /// SceneGenerator: onClick.AddListener registers a non-persistent
        /// listener, which is not serialized into the saved scene, so wiring
        /// it at edit time would silently produce dead buttons.
        /// </summary>
        private void Start()
        {
            gameId = GameManager.Instance != null ? GameManager.Instance.GameId : string.Empty;
            shopTransition = shopPanel != null ? shopPanel.GetComponent<PanelTransition>() : null;

            if (openButtons != null)
            {
                foreach (Button open in openButtons)
                {
                    if (open != null) open.onClick.AddListener(Open);
                }
            }

            if (coinMagnetButton != null) coinMagnetButton.onClick.AddListener(HandleCoinMagnetClicked);
            if (redSkinButton != null) redSkinButton.onClick.AddListener(HandleRedSkinClicked);
            if (coinPackButton != null) coinPackButton.onClick.AddListener(HandleCoinPackClicked);
            if (removeAdsButton != null) removeAdsButton.onClick.AddListener(HandleRemoveAdsClicked);
            if (closeButton != null) closeButton.onClick.AddListener(Close);

            if (MonetizationService.Instance != null)
            {
                MonetizationService.Instance.PurchaseSucceeded += HandlePurchaseSucceeded;
                MonetizationService.Instance.AvailabilityChanged += Refresh;
            }

            if (shopPanel != null) shopPanel.SetActive(false);
            Refresh();
        }

        public void Open()
        {
            if (shopTransition != null) shopTransition.Show();
            else if (shopPanel != null) shopPanel.SetActive(true);

            Refresh();
        }

        public void Close()
        {
            if (shopTransition != null) shopTransition.Hide();
            else if (shopPanel != null) shopPanel.SetActive(false);
        }

        private void HandleCoinMagnetClicked()
        {
            if (IsOwned(ShopKeys.CoinMagnetOwned)) return;
            TryPurchase(ShopKeys.CoinMagnetOwned, ShopKeys.CoinMagnetCost);
        }

        private void HandleRedSkinClicked()
        {
            if (!IsOwned(ShopKeys.RedSkinOwned))
            {
                TryPurchase(ShopKeys.RedSkinOwned, ShopKeys.RedSkinCost);
                return;
            }

            bool equipped = SaveSystem.GetInt(gameId, ShopKeys.RedSkinEquipped) != 0;
            SaveSystem.SaveInt(gameId, ShopKeys.RedSkinEquipped, equipped ? 0 : 1);
            Refresh();
        }

        private void HandleCoinPackClicked() => MonetizationService.Instance?.PurchaseCoinPack();
        private void HandleRemoveAdsClicked() => MonetizationService.Instance?.PurchaseRemoveAds();
        private void HandlePurchaseSucceeded(string productId) => Refresh();

        private void TryPurchase(string ownedKey, int cost)
        {
            int currency = SaveSystem.GetInt(gameId, ShopKeys.Currency);
            if (currency < cost) return;

            SaveSystem.SaveInt(gameId, ShopKeys.Currency, currency - cost);
            SaveSystem.SaveInt(gameId, ownedKey, 1);
            Refresh();
        }

        private bool IsOwned(string key) => SaveSystem.GetInt(gameId, key) != 0;

        private void Refresh()
        {
            int currency = SaveSystem.GetInt(gameId, ShopKeys.Currency);
            if (currencyText != null) currencyText.text = currency.ToString("N0", CultureInfo.InvariantCulture);

            bool magnetOwned = IsOwned(ShopKeys.CoinMagnetOwned);
            if (coinMagnetButtonLabel != null)
            {
                coinMagnetButtonLabel.text = magnetOwned ? "보유중" : $"{ShopKeys.CoinMagnetCost}";
            }
            // Dimmed but still priced: hiding an item the player cannot afford
            // would hide the reason to keep playing. Owned is disabled too -
            // there is nothing left to do with it.
            SetAffordable(coinMagnetButton, !magnetOwned && currency >= ShopKeys.CoinMagnetCost);

            bool skinOwned = IsOwned(ShopKeys.RedSkinOwned);
            if (redSkinButtonLabel != null)
            {
                if (!skinOwned)
                {
                    redSkinButtonLabel.text = $"{ShopKeys.RedSkinCost}";
                }
                else
                {
                    redSkinButtonLabel.text = SaveSystem.GetInt(gameId, ShopKeys.RedSkinEquipped) != 0 ? "장착됨" : "장착";
                }
            }
            // An owned skin stays live - the button toggles equip/unequip.
            SetAffordable(redSkinButton, skinOwned || currency >= ShopKeys.RedSkinCost);

            bool storeReady = MonetizationService.Instance != null && MonetizationService.Instance.IsStoreReady;
            if (coinPackButtonLabel != null) coinPackButtonLabel.text = storeReady ? "500 코인" : "스토어 준비 중";
            SetAffordable(coinPackButton, storeReady);

            bool adsRemoved = SaveSystem.GetInt(gameId, MonetizationService.RemoveAdsKey) != 0;
            if (removeAdsButtonLabel != null)
                removeAdsButtonLabel.text = adsRemoved ? "구매 완료" : storeReady ? "광고 제거" : "스토어 준비 중";
            SetAffordable(removeAdsButton, storeReady && !adsRemoved);
        }

        private static void SetAffordable(Button button, bool affordable)
        {
            if (button != null) button.interactable = affordable;
        }

        private void OnDestroy()
        {
            if (MonetizationService.Instance == null) return;
            MonetizationService.Instance.PurchaseSucceeded -= HandlePurchaseSucceeded;
            MonetizationService.Instance.AvailabilityChanged -= Refresh;
        }
    }
}
