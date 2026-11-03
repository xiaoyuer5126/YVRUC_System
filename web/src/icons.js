/** 全局注册的 Element Plus 图标，模板里直接写 <Search /> 用的就是这些。
 *
 *  只列真正用到的：整包两百多个图标，一把全注册进去，没人用的那两百个也会编进
 *  首屏那一个文件，白下一百多 KB。
 *
 *  模板里要用一个还没在这儿列出的图标，把名字加到下面；漏了的
 *  `scripts/vuecheck.py` 会报「用了没注册的组件」——图标没注册时 Vue 只是
 *  静默渲染成空，页面上少一个图标，构建和测试都不吭声。
 */
export {
  ArrowDown,
  Bell,
  Calendar,
  ChatDotRound,
  Check,
  Clock,
  Compass,
  Document,
  Download,
  Fold,
  Key,
  Link,
  List,
  Loading,
  Location,
  Lock,
  MagicStick,
  Odometer,
  OfficeBuilding,
  Paperclip,
  Plus,
  Promotion,
  QuestionFilled,
  Refresh,
  Search,
  Stamp,
  Tickets,
  Upload,
  User,
  UserFilled,
  Warning,
  WarningFilled,
} from '@element-plus/icons-vue'
