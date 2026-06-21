import { Icon, type IconName, type IconSize } from "@/components/ui/Icon";
import {
  NAV_GROUP_ICONS,
  NAV_ICONS,
  type NavGroupIconId,
} from "@/lib/nav-icons";

type NavIconProps = {
  href?: string;
  groupId?: NavGroupIconId;
  size?: IconSize;
  className?: string;
};

export function NavIcon({ href, groupId, size = "md", className }: NavIconProps) {
  let name: IconName = "dot";
  if (groupId && groupId in NAV_GROUP_ICONS) {
    name = NAV_GROUP_ICONS[groupId];
  } else if (href && href in NAV_ICONS) {
    name = NAV_ICONS[href];
  }
  return <Icon name={name} size={size} className={className} />;
}
