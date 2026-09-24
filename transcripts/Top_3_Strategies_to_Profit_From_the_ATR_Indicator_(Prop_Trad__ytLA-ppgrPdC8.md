# Top_3_Strategies_to_Profit_From_the_ATR_Indicator_(Prop_Trad



- 來源：[YouTube 影片](https://www.youtube.com/watch?v=LA-ppgrPdC8)

- 辨識：Groq:whisper-large-v3-turbo

- 統計：11086 字 / 82 段

- 分類：新手教學

- 關鍵字：ATR指標、交易策略、波段操作

- 短標題：Top 3 Strategies to Profit From the ATR Indicator (Prop Trad



## 摘要



**一句話**：ATR指標可用於衡量波動性、設定停損、比較股票，並在不同市場條件下動態調整風險。

- ATR是以最高價、最低價與前一收盤價三者最大差值的平均值計算，用於衡量市場波動性。  
- 透過將價格變動以ATR單位標準化，可比較同一股票或不同股票的相對波動，判斷變動是否顯著。  
- ATR常用於衡量突破或移動幅度、跨股票比較ATR變動，以及設定如3ATR追蹤停損等風險管理工具。  
- ATR隨波動性調整，波動升高時ATR變寬、停損距離拉遠；波動下降時ATR變窄、停損距離縮短，實現動態風險管理。  
- 以ATR的10%作為止損距離，例如在PDD案例中使用10% ATR作止損線。  
- 在CMG高價位（約1000）時ATR可提供合適止損距離；對低價股票則可能僅提供幾分錢距離。  
- VFS昨日出現五天連續成交量增加的超度擴張，ATR可用於測試此類情況。  
- 參與者可免費報名一小時線上簡報，並有三項簡單標準可獲得面試機會。



---



**[00:00]** The ATR indicator is one of the most widely used indicators on our prop desk and there's a good reason for this.

**[00:07]** In this video, one of our most seasoned prop traders, Garrett Drinan, will break down the three most effective ways to use this valuable indicator.

**[00:17]** I'm Mike Belafuri and we're one of the top proprietary trading firms located in New York City and proud to have developed number seven and even eight figure per year traders. 希望你同意,

**[00:29]** 這就是最高YouTube的頻道幫助你增加你的股票

**[00:47]** Garrett here from the New York City desk and today we're gonna talk about ATR so we've gotten tons of requests lately

**[00:55]** to do a video on ATR especially after doing the Keltner channel video which was about an indicator

**[01:02]** based on ATR and so you can check that out on our channel and so we're gonna we're gonna get a

**[01:08]** little nerdy today there's some really great stuff here rooted in statistics but also super basic

**[01:15]** and directly applicable to trading whether you're a model builder a systems trader or a fully 一個強烈品名就是推廣,它是很重要的。 在這個影片中,你會知道什麼是, 如何最多的投資人在 our Desk, 用ATR每天前來投資的第一天, 而且我希望大家有提到有趣的想法

**[01:44]** 如何使用 it。 再到這個影片中,我們會講一下三个人的实用选择这种课题是用选择的专业的课题首先,我们进入这个课题要解释了解释的什么ATR是和什么重要的ATR是ATR是几乎的几乎它是代表的借购的借购这是最重要的它是一部分的经验我们的经营有些方法, 有些方法,你能够试图的股票,但这就是很简单的方法。 基本上,看来,看来, 它是在这里的量度,

**[02:31]** 所以在这里,你能看到高和低, 而在ATR,基本上,是在这里的量度, 是在这里的量度和加上的量度。 所以,一小说, 一位华企业的华企业,他叫 Tim, 他昨天说他有点好在谈到他的依赖友, 人们没关系了,他在谈到我说, 他在谈到这种情感觉, 因为他与他们聊天的时候, 他们在谈论提论的息子, 在谈到股市和股市秘密的移动, 他在谈启书说,他们说,

**[03:12]** 我说,你与你打开, Tim? 当然他们对他们的货币是对他们的货币人知道他才不知道什么他就只能把这些货币的货币说明他就不会说明他在中国上有任何货币那么就这样就像是在我们的货币的货币有我们的货币在我们就在多数的时候我们在做什么都会认为这个货币的货币是做出的货币是在什么在每个月的货币的货币上了一项目的货币就能稳定

**[03:40]** specifically how this is developed so it's the average range between each bars high and low

**[03:47]** over a specific number of periods so on the right here is the actual formula and you can pause the

**[03:52]** screen and kind of write this down you could go into your trading platform and any trading platform

**[03:58]** is going to have an atr indicator um you know i use one right on thinkorswim and if you look into

**[04:05]** the code this is basically what it's going to be doing and it's the true range which is the max of

**[04:12]** the high minus the low or the high minus the previous close or the low minus the previous close

**[04:18]** and that's basically saying the range of the candle including any gaps that come from the

**[04:25]** previous candle and then you just take the average of that over a time period okay so like i said any 每一步的距離方法都可以用這個你可以用這個在任何時機例如,在一個一-minute調在一-minute調,是一-minute距離的一-minute距離

**[04:42]** 在這個講題裡,我們會主要主要用這個距離的距離是一個距離距離的距離距離而是一個距離距離的距離距離所以,一件事是我們前面的主要說对这种种程式使用我们能够理一下, 其实它就是公平的原因, 因为我们在这个界线上的设计 例如非常好 流氓的反应。 我们在平均上可以比较相互和相互相互数 。 我们在这些设计方式作为打击到与其他方向的投资予的并渠现了一种?

**[05:27]** 如果用ATR去平均乱,我们会把一个公司的公司的公司比较高的公司的公司这就是非常非常用适当我们的公司和不同的公司和不同的公司所以,例如,一$5的公司的公司基本上是一$1的公司是非常优秀的公司while the same $5 move on a stock with an ATR of $10 would be less notable.

**[06:00]** By comparing a stock's current price movement to its ATR, you're effectively normalizing its movement relative to its typical behavior.

**[06:08]** And this allows traders to gauge the significance of a price movement more accurately. So by normalizing using ATR, traders can compare movements on a relative scal試動力量甚至是用力駛和不同的价格

**[06:24]** 或者不同的物質就是一小比普通通用就是要来解放Tesla會有很多大量ATR 因為特朗普通過普通通過如果特朗普通可以用 $10 Ford is going to have like a 25 cents ATR and that represents how much more on average

**[06:50]** and this is daily, these are daily ATRs how much more on average Tesla moves than Ford so not only is Tesla a higher priced stock than Ford

**[07:00]** but it also makes larger percentage moves on average so this is kind of a no brainer like we know Tesla is going to move more than Ford but sometimes it's more subtle

**[07:09]** so something like Berkshire Hathaway BRK,是一額高市值的, 在3.60元的, 在4.40元的, $4.40元。 像AMC,是一額高市值的, 在11元的, 在4.50元的, 所以,BRKB是一額高市值的, 他們每次都差不多同樣的ATR, 在在4.50元的, 所以这就是在平均上的一天,他们在平均上的一天,

**[07:47]** 所以这就是一天,一个ATR可以说我们的一天, 如果我们看这些 stocks,我们可以看到一个市场可以在平均上的一天,不需要有一个大量ATR, 而一个市场可以在平均上的一天,不需要有一个大量ATR。 there are three ways to normalize using ATR so we can compare the stock to itself

**[08:14]** so we can say this stock today is moving three times as much as it usually moves that's how we can measure that using ATRs to move three ATRs off the open

**[08:25]** is something that we might say we can compare stocks across the market multiple stocks, we can compare stocks to each other 那是像我们刚才做的,对于车上的车上, 如果我们看一样的公司,我们可以说, 你会说,你会说,

**[08:44]** Google只移动两ATRs, 而 Microsoft只移动一, 我们不需要计算, 我们不需要计算, 什么是最高的价值, 什么是最高的价值, 这ATR就移动了这种移动到最简单的比较简单 possible. 然後我們可以加上測試的指標這就是比較有一點點的,比較有一點點我們不會再加進去的資料但基本的概念是一種指標的指標,或是一種的指標是一種的指標的指標,

**[09:22]** 當然是指標的指標, 所以你可能會有很高的指標that moves a lot feeding information into an indicator like you know an RSI, MACD, like whatever

**[09:34]** it is and that indicator might act quite a bit different because it's a higher price more volatile

**[09:42]** stock that's feeding information into it so if you're interested in normalizing the activity of

**[09:49]** that indicator across stocks you might actually use ATR instead of price movements okay and so Tim Masters is a really smart algorithmic trading expert.

**[10:04]** He has a great book called Statistically Sound Indicators for Financial Market Prediction.

**[10:10]** He said,and this just speaks to how important ATR is,he says,how do we measure price volatility for scaling?

**[10:18]** There are an infinite number of choices,but my own favorite is average true range,which I believe best reflects true market volatility for scaling most indicators.

**[10:28]** So he likes ATR for this. There are other things that we can use to normalize movements in stocks, stuff like standard deviation, which is great, but that's a crazy formula.

**[10:42]** I mean, who the hell knows how to write that formula? We're all traders. We want simple tools that speak directly to the goal we're trying to achieve, and ATR does this really well.

**[10:53]** 所以,大家可以進入了一些例子, 例如昨天, 在用了這件事所以, 雖然這些都是在這件事我看了, 這就是了這個人的 trade 我們就進入了一些這些有三個方法要用ATR 一種方法, 你可以用他們的接受你可以用他們接受受受接受你可以用他們接受受到負責所以,這就是

**[11:22]** what we're going to go through here with some examples so here's AFRM this is a daily chart

**[11:28]** and this is yesterday yesterday's close okay so you can see three days ago it had a big breakout

**[11:36]** and that was earnings so big volume big breakout and then an inside day the next day and then day

**[11:42]** three a continuation breakout right and at the firm will call this a day three breakout where where it breaks above the earnings day high after an inside day.

**[11:52]** And there was a good amount of volume on this day, but nothing compared to day one. But this is a pretty good textbook day three breakout pattern that we're gonna look at.

**[12:02]** And so often when something like this goes off the open and we're in it, right? We bought that breakout. We wanna know, you know, where can I expect this to go?

**[12:13]** Like, where can I start to look to take scales, right? 所以用ATR是一方式,我们会看一样的标准, 对吧,什么是一方式的地方,

**[12:25]** 我们可以做这些地方,我们可以做一个大量ATR,我们可以拆开一个大量ATR,我们可以拆开一个大量ATR,我们可以拆开一个大量A入我们的照片我们认识看是1ATR我们全部扩上去我们的圈谷创线给我们来的价值那会让我们来得到这个地方可以的地下来1ATR 最后来的价格1ATR 再多When它购买1 hour 1日趷值那是一很好的動作一件事要留意的確是在更多的方法,

**[13:07]** 更多的可能性它會 surpass一ATR 可以做過多數量的ATR 所以在一天一動作我們可能會要看一半,兩,三ATR 在一天三動作, 一種少量度, 我們可能會只要看一ATR 像 AFRM did 在外面的開放所以就像在一种比较多,就看了看, 看了看,你可能会有什么样的。 然后,一个东西可以做,是用了ATR trailing stop。

**[13:44]** 这是一个不同的 concept, 它是一个方式的方式, 一个方式的方式, 是一个提供的方式, 你能够得到任何方式, 我认为 Thinkorswim它和其他方式, 所以这就是什么它看起来这个是一个指导的使用ATR 它看起来是一个扫养的扫养一个扫养的扫养在这个时候,三个扫养的扫养所以这就是三个扫养的扫养从最低扫养的扫养它是扫养的扫养

**[14:23]** the great part about this is that as we know ATR adjusts for the volatility of the stock so the more volatile the stock the looser the stock

**[14:33]** if the stock gets more volatile the ATRs will adjust for that and it'll start to get priced into the indicator

**[14:40]** and if volatility contracts the same thing will happen and the indicator will get tighter and tighter 它是很棒的方法,如果你有 trouble在一 trade,用一ATR trailing stop, 它可以保持你在一旦比你更多,如果你不用它,

**[14:59]** 你可以玩到不同的設定, 你能看這一個三ATR,三-period trailing stop, 你能看到它是一位一ATR,只要是一位一ATR, here's another example from yesterday pdd had earnings and this was a day one earnings and this

**[15:22]** exploded off the open on a lot of volume and if you remember earlier i said that when a stock is

**[15:30]** doing multiples of average volume we're going to give it a little bit more room to go because

**[15:36]** volume is like fuel so especially with a with an earnings catalyst and a lot of relative volume 所以我们将来1ATR应该说一几个好一个在一块如果它是确实上它是分析一个好一块所以PDD会7出来那第一个大约1ATR是1.5 这就是1.5%的一条但是你跟他的部位出现

**[16:04]** 你会有配合设计你会找到你的设定他们每个人的价格都则1ATR 那是一位你也想要去打算你可能会看到你会有很大的热烈在业务的业务上的业务上你会看到你会有2ATR或是3ATR 你会有多多的业务的这些都是例如我们可以看这些开放的在开放的开放然后再看一看, 好,这些是什么同时,我们就在看到这些人的看法。 我们的评论,移动了一段时间。

**[16:48]** 同时,我们的三个一-minute bar ATR, 从最后的三个人的关系。 我们的同时,它会在推出一个同时,然后,它会在排出一段时间。 好了, here's PDD on the same day, we're just zooming out a little bit, and we've drawn

**[17:11]** a pre-market level that was a pretty significant level in the morning before the open. And if you were trading this and you were kind of buying it like off the open, say like

**[17:21]** on the first candle, and you wanted to put a stop below this significant pre-market level, 你可能会把这些高度的高度但你也可能会给它一种更多的地方因为我们知道我们的 stocks喜欢在这些高度的高度然后在这些高度的高度所以如果我会在这些高度的高度我认为这些高度的高度

**[17:45]** 我会有些高度的高度我会有些高度的高度我会在这些高度的高度我真的想要看它高度的高度我会在这些高度的高度我会不会只要放到高度的高度在那一層的高度上, 因為我會在那層的高度上, 特別是931,932, 那是真的什麼事, 所以,一件事你能做, 是你能用它的日常ATR 是一個重點, 你能用一百分的那一層並且伸到那層的高度上, 所以,它是給它們的高度上的高度上,

**[18:25]** 如果你是在文字上,如果你是在文字上, 你会知道,在某些程度上, 这就是很好的课程, 你可以用ATR去 inform 到某些程度上, 到某些程度上, 你能够购买不同的课程, 你能够用ATR去平均的距离,所以这个PDD的课程是会有一定的级,所以在这个时候10%的大约的ATR是这个PDD的高度是有一定的线所以在这个时候10%的日子的低线是会员10%的低线的平线

**[19:05]** 所以如果做这个CMG的高价值这会是在1000的高价值这会是一个比较高的距离but relatively speaking it's going to give it an appropriate distance from that stop.

**[19:22]** And again, like if you're trading a really low price stock, it's going to do the same thing where it might actually just give you a few pennies.

**[19:30]** Okay, so it's a great way to adjust that space if you're trading a lot of different tickers.

**[19:35]** All right, and here's our third example. This is VFS from yesterday, and this was an over extension play.

**[19:41]** You can see it went five days in a row as volume started to come into the stock. 這裡有一個可以做的ATR 去給一個測試的測試在每個月的超過測試在每個月的測試你可以想想想好,這麼多的時間在五個月所以在這個時候如果有四個月的ATR 在五個月的五個月你會得到那一塊月的這就是一個月的

**[20:13]** 這就是一個月的測試如果有一個月的設置可以回到,可以看,好多久,都要去看, 我可以用5,这个一半,很近,很近,

**[20:27]** 但是这些是好多,他们是好多,只是要去看, 如果它只能到3,你能知道,你会知道, 那是不宗耐的,以为我所拥有的例子, 和我所拥有的领域的领域, 就像是1日,1日,1日就能看到了比例, 这就是一个游戏的游戏, 我们问一下, 每天ATR,每天ATR,有这些价值的价值在5天? 你将下来的高从5天后的数据, 你将下来的数据,在这一切业的高, 在这个时候4.5%的高,

**[21:10]** 它是供应率的,它是供应率的, 它是供应率的, 然后,有些人的书, 有些人的书, 也有一个人的书, 这些书是一个公平的书, 我们在一个公平的书, 我们在一个公平的书, 我们在一个公平的书, 我们在书, 因为这些书, 这些书是书, 如果您想看这个书, 我们可以在书上的书, 所以你看,你能看到这个评论可以用在很多情况下, 如果你想要建设定的权利用来的权利用,

**[21:48]** 这个是一种手术的权利用, 如果你是一种完全的创业, 可以做出的创业,这个可以用来做出的一种关系。 所以,在最最后, 它是一种方式的方式的方式, 所以如果你想要解决的接触, 或者是一种方式的方式, 我会把你的投资方法的交流是一种方法的方法所以请按下留言我会给你解答的我们喜欢你如何用ATR 我会用你投资的方法我会用你投资的方法你会用你的投资方法

**[22:28]** 把我的投资方法都给你是什么给你这个公司的方法你可以追我投资方法在下面的影片中, 讓你來看

**[23:04]** 一年一月我去我的 office 所以我可以分享更多你可能会看到一些浪费划流量费用轮廓费用轮廓一天甚至 15分钟然后他们在游戏在一个游戏的游戏在一天我可以告诉你我们的费用轮廓像是跑选择他们活动他们活动和继续在我企業,我們在我們的業務之中我們有它需要這次的決定在這遊戲的表面我是一個大家的割副副創作者和主管的雙倍副創作者一面的最多經驗局在我們香港裡的我們

**[23:48]** 我們一直在賠資金找來找來找的不只為我們在你的樓上返的地方我們在它的家裡我們來找這一家我們有新的資料都做的讓他們去做了7個月的資料在負責到負責到負責任的資料但是要求能力的資料你需要有這個資料這就是為了我們做一個新的訓練我們分享會如何能夠成為SNB成為一個資料的資料負責責責任利責責任責任和獲得到所有資料的資料

**[24:30]** and resources. And the best part, you don't have to be a profitable trader yet. In fact, we prefer to

**[24:38]** mold profitable traders with our methods and our techniques. That's why we have just three simple

**[24:44]** criteria that can earn anyone an interview. We're looking for highly ambitious and determined

**[24:50]** traders who fit our culture first and foremost. So if you believe that could be you, sign up for

**[24:56]** the free one hour online presentation by clicking the link that's in your top right corner of your screen now