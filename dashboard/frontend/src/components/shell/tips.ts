/**
 * Shows a kit button's disabled-reason bubble below the button instead of above it: in the 48 px header
 * the kit's bubble (always above) would be cut off by the top of the window. Put it on a wrapper.
 */
export const TIP_BELOW =
  '[&_[role=tooltip]]:bottom-auto [&_[role=tooltip]]:top-full [&_[role=tooltip]]:mb-0 [&_[role=tooltip]]:mt-2'
