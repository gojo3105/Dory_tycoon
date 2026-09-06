using System;
using UnityEngine;

namespace GameFactory.Core
{
    /// <summary>
    /// Single "tap anywhere" detector shared by every genre. Reacts to Android
    /// touch input and to the mouse so gameplay can be tested in the Editor.
    /// One MonoBehaviour, one Update() call, regardless of how many systems
    /// need to react to a tap.
    /// </summary>
    public class TapInput : MonoBehaviour
    {
        public static event Action Tapped;

        /// <summary>A downward swipe, or the Down arrow in the Editor.
        ///
        /// The second verb the runner genre is built on. With only a jump,
        /// every obstacle is the same question - "press now?" - and the game
        /// reads as one note repeated. A duck turns the level into a
        /// conversation: high things to go under, low things to go over.
        /// </summary>
        public static event Action SwipedDown;

        /// <summary>The swipe ended, or the Down arrow was released.</summary>
        public static event Action SwipeReleased;

        [Tooltip("Screen fraction a drag must cover downward to count as a swipe.")]
        [SerializeField] private float swipeFraction = 0.06f;

        // A press is only a tap once it is clear it is NOT a swipe. Firing
        // Tapped on touch-down would make every duck also jump.
        private bool pressActive;
        private bool swipeFired;
        private Vector2 pressStart;

        private float SwipeThreshold => Screen.height * swipeFraction;

        private void Update()
        {
            // Editor and desktop: arrow key, so the duck is testable without
            // a touchscreen. Held rather than tapped - a duck has a duration.
            if (Input.GetKeyDown(KeyCode.DownArrow)) SwipedDown?.Invoke();
            if (Input.GetKeyUp(KeyCode.DownArrow)) SwipeReleased?.Invoke();

            if (Input.touchCount > 0)
            {
                HandlePointer(Input.GetTouch(0).position,
                              Input.GetTouch(0).phase == TouchPhase.Began,
                              Input.GetTouch(0).phase == TouchPhase.Ended
                              || Input.GetTouch(0).phase == TouchPhase.Canceled);
                return;
            }

            HandlePointer(Input.mousePosition,
                          Input.GetMouseButtonDown(0), Input.GetMouseButtonUp(0));
        }

        private void HandlePointer(Vector2 position, bool began, bool ended)
        {
            if (began)
            {
                pressActive = true;
                swipeFired = false;
                pressStart = position;
                return;
            }

            if (!pressActive) return;

            if (!swipeFired && pressStart.y - position.y >= SwipeThreshold)
            {
                swipeFired = true;
                SwipedDown?.Invoke();
            }

            if (ended)
            {
                pressActive = false;
                if (swipeFired) SwipeReleased?.Invoke();
                else Tapped?.Invoke();
            }
        }
    }
}
