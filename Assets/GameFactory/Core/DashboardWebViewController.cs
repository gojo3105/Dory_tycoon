using UnityEngine;

namespace GameFactory.Core
{
    /// <summary>Shows the live AI control panel inside the Android app.</summary>
    public sealed class DashboardWebViewController : MonoBehaviour
    {
        [SerializeField] private string serverUrl = "http://10.0.2.2:8765/";

#if UNITY_ANDROID && !UNITY_EDITOR
        private AndroidJavaObject activity;
        private AndroidJavaObject webView;
#endif

        private void Start()
        {
#if UNITY_ANDROID && !UNITY_EDITOR
            OpenWebView();
#else
            Debug.Log($"[DashboardWebView] Android preview URL: {serverUrl}");
#endif
        }

#if UNITY_ANDROID && !UNITY_EDITOR
        private void OpenWebView()
        {
            using (AndroidJavaClass unityPlayer = new AndroidJavaClass("com.unity3d.player.UnityPlayer"))
            {
                activity = unityPlayer.GetStatic<AndroidJavaObject>("currentActivity");
            }

            activity.Call("runOnUiThread", new AndroidJavaRunnable(() =>
            {
                webView = new AndroidJavaObject("android.webkit.WebView", activity);
                webView.Call("setBackgroundColor", unchecked((int)0xFF000000));
                AndroidJavaObject settings = webView.Call<AndroidJavaObject>("getSettings");
                settings.Call("setJavaScriptEnabled", true);
                settings.Call("setDomStorageEnabled", true);
                webView.Call("setWebViewClient", new AndroidJavaObject("android.webkit.WebViewClient"));

                using (AndroidJavaClass layoutParams = new AndroidJavaClass("android.view.ViewGroup$LayoutParams"))
                {
                    int matchParent = layoutParams.GetStatic<int>("MATCH_PARENT");
                    AndroidJavaObject parameters = new AndroidJavaObject(
                        "android.view.ViewGroup$LayoutParams", matchParent, matchParent);
                    activity.Call("addContentView", webView, parameters);
                }

                webView.Call("loadUrl", serverUrl);
            }));
        }

        private void OnDestroy()
        {
            if (activity == null || webView == null)
            {
                return;
            }

            activity.Call("runOnUiThread", new AndroidJavaRunnable(() =>
            {
                webView.Call("stopLoading");
                webView.Call("destroy");
                webView = null;
            }));
        }
#endif
    }
}
