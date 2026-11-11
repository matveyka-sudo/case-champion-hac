import { NavigationLink } from "../shared/NavigationLink";
import { useState } from "react";
import { Icon } from "../shared/ui";
import { css } from "../shared/format";
import { MiniGrid, TransportShowcase } from "../widgets/TransportShowcase";
import { PreviewMap } from "../widgets/RussiaMap";
import { useLandingMotion } from "../features/motion";
export function LandingPage({ dataMode = "demo" }: { dataMode?: "demo" | "api" }) {
  const [group, setGroup] = useState("pax");
  useLandingMotion();
  return (
    <div id="landing">
      <header className="lp-header" id="lpHeader">
        <NavigationLink
          className="wordmark"
          to="top"
          aria-label="Waydex — в начало страницы"
        >
          <span className="wm-way">{"Way"}</span>
          <span className="wm-dex">{"dex"}</span>
        </NavigationLink>
        <nav className="lp-nav" aria-label="Основная навигация">
          <NavigationLink className="nav-link-wx" to="about">
            {"О продукте"}
          </NavigationLink>
          <NavigationLink className="nav-link-wx" to="features">
            {"Возможности"}
          </NavigationLink>
          <NavigationLink className="wbtn wbtn-light" to="atlas">
            {"Открыть атлас "}
            <span className="arrow">
              <Icon name="arrow-up-right" />
            </span>
          </NavigationLink>
        </nav>
      </header>
      <main id="main">
        <section className="hero" id="top" aria-labelledby="heroTitle">
          <div className="hero-media" aria-hidden="true">
            <img
              src="/assets/hero-world.jpg"
              width="2644"
              height="1798"
              alt=""
              fetchPriority="high"
              decoding="async"
            />
          </div>
          <div className="hero-grid" aria-hidden="true"></div>
          <svg
            className="trace"
            style={css({ left: "0", top: "0", width: "100%", height: "100%" })}
            viewBox="0 0 1440 900"
            preserveAspectRatio="none"
            aria-hidden="true"
            data-reveal="draw"
          >
            <defs>
              <linearGradient id="gTrace" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stopColor="#C9BEFF" stopOpacity="0"></stop>
                <stop offset=".4" stopColor="#C9BEFF"></stop>
                <stop offset="1" stopColor="#3AD7F2"></stop>
              </linearGradient>
            </defs>
            <path
              className="t-dash"
              d="M820 120 C 980 260, 1120 260, 1300 420"
            ></path>
            <path
              className="t-main"
              style={css({ "--len": "1500" })}
              d="M980 900 C 1040 780, 1080 700, 1150 580 S 1290 380, 1440 320"
            ></path>
            <circle className="node" cx="1150" cy="580" r="5"></circle>
            <circle className="node" cx="1300" cy="420" r="3.5"></circle>
          </svg>
          <div className="hero-inner">
            <span className="eyebrow on-dark">
              {"Транспортная связанность · Россия"}
            </span>
            <h1 className="hero-title" id="heroTitle">
              <span className="ln">
                <span>{"Связи территорий."}</span>
              </span>
              <span className="ln accent">
                <span>{"Понятные решения."}</span>
              </span>
            </h1>
            <p className="hero-lead">
              {
                "Waydex показывает, за сколько минут транспорт довозит людей до поликлиник, школ и вокзалов, как связаны города и регионы и где грузам не хватает инфраструктуры. Для жителей, аналитиков и органов управления."
              }
            </p>
            <div className="hero-cta">
              <NavigationLink className="wbtn wbtn-primary" to="atlas">
                {"Открыть атлас "}
                <span className="arrow">
                  <Icon name="arrow-up-right" />
                </span>
              </NavigationLink>
              <NavigationLink className="wbtn wbtn-ghost-light" to="how">
                {"Как это работает"}
              </NavigationLink>
            </div>
            <div className="hero-facts" aria-label="Что внутри">
              <span>
                <b>{"3 режима"}</b>
                {" · город, пассажиры, грузы"}
              </span>
              <span>
                <b>{"9 категорий"}</b>
                {" городских объектов"}
              </span>
              <span>
                <b>{"15 минут"}</b>
                {" — шаг времени расчёта"}
              </span>
            </div>
          </div>
          <NavigationLink
            className="hero-scroll"
            to="about"
            aria-label="Прокрутить к описанию продукта"
          >
            <span className="line"></span>
            {"Дальше"}
          </NavigationLink>
        </section>
        <section className="sec about" id="about" aria-labelledby="aboutTitle">
          <svg
            className="trace"
            style={css({
              left: "-2%",
              top: "-40px",
              width: "104%",
              height: "420px",
            })}
            viewBox="0 0 1440 420"
            preserveAspectRatio="none"
            aria-hidden="true"
            data-reveal="draw"
          >
            <path
              className="t-main"
              style={css({ "--len": "1700" })}
              d="M760 0 C 700 120, 520 160, 380 210 S 120 300, 0 360"
            ></path>
            <circle className="node" cx="380" cy="210" r="5"></circle>
          </svg>
          <div className="wrap">
            <div className="about-grid">
              <div data-reveal="up">
                <span className="eyebrow on-dark">{"О продукте"}</span>
                <h2 className="about-title" id="aboutTitle">
                  {"Близко на карте —"}
                  <br />
                  <em>{"не значит доступно."}</em>
                </h2>
                <p className="sec-lead">
                  {
                    "Waydex считает не километры, а полный путь: дойти до остановки, дождаться, доехать, пересесть и снова дождаться. Так видно, где именно теряется время и сколько жителей это касается."
                  }
                </p>
                <div className="formula">
                  <span>{"Индекс точки"}</span>
                  <code>
                    {"I = 100 × 2"}
                    <sup>{"−T/H"}</sup>
                  </code>
                  <span>{"H — 20, 30, 40 или 60 минут по площади города"}</span>
                </div>
              </div>
              <article
                className="pathcard"
                data-reveal="up"
                aria-label="Пример: путь до поликлиники"
              >
                <div className="pathcard-top">
                  <b style={css({ fontSize: "17px" })}>
                    {"Путь до ближайшей взрослой поликлиники"}
                  </b>
                  <span className="demo-tag">
                    <Icon name="flask-conical" />
                    {"Демонстрационный пример"}
                  </span>
                </div>
                <div className="pathcard-ends">
                  <div className="pend">
                    <b>{"Точка сетки"}</b>
                    <span>{"ячейка 1 км"}</span>
                  </div>
                  <div className="crow" aria-hidden="true"></div>
                  <div className="pend">
                    <b>{"Поликлиника"}</b>
                    <span>{"ближайшая по времени"}</span>
                  </div>
                </div>
                <div
                  className="pathbar"
                  id="pathbar"
                  role="img"
                  aria-label="Пешком 6 минут, ожидание 4, автобус 9, переход 2, ожидание пересадки 11, трамвай 8, пешком 4. Всего 44 минуты."
                >
                  <div
                    className="pseg walk"
                    style={css({ "--m": "6", transitionDelay: "0s" })}
                  >
                    <span>{"6"}</span>
                  </div>
                  <div
                    className="pseg wait"
                    style={css({ "--m": "4", transitionDelay: ".08s" })}
                  >
                    <span>{"4"}</span>
                  </div>
                  <div
                    className="pseg ride"
                    style={css({ "--m": "9", transitionDelay: ".16s" })}
                  >
                    <span>{"автобус 9"}</span>
                  </div>
                  <div
                    className="pseg xfer"
                    style={css({ "--m": "2", transitionDelay: ".24s" })}
                  >
                    <span>{"2"}</span>
                  </div>
                  <div
                    className="pseg hot"
                    style={css({ "--m": "11", transitionDelay: ".32s" })}
                  >
                    <span>{"ожидание 11"}</span>
                  </div>
                  <div
                    className="pseg ride2"
                    style={css({ "--m": "8", transitionDelay: ".40s" })}
                  >
                    <span>{"трамвай 8"}</span>
                  </div>
                  <div
                    className="pseg walk"
                    style={css({ "--m": "4", transitionDelay: ".48s" })}
                  >
                    <span>{"4"}</span>
                  </div>
                </div>
                <div className="pathlegend">
                  <div className="pl">
                    <b className="tnum">{"10"}</b>
                    {"пешком, мин"}
                  </div>
                  <div className="pl">
                    <b className="tnum">{"4"}</b>
                    {"первое ожидание"}
                  </div>
                  <div className="pl">
                    <b className="tnum">{"19"}</b>
                    {"в пути и переходе"}
                  </div>
                  <div className="pl hot">
                    <b className="tnum">{"11"}</b>
                    {"ожидание пересадки"}
                  </div>
                </div>
                <div className="pathtotal">
                  <span className="big tnum">{"44 мин"}</span>
                  <p>
                    {
                      "Ожидание пересадки — четверть пути. Именно его агент предложит проверить сценарием согласования отправлений."
                    }
                  </p>
                </div>
              </article>
            </div>
          </div>
        </section>
        <section
          className="sec stage"
          id="features"
          aria-labelledby="featTitle"
        >
          <div
            className="bg-halo"
            aria-hidden="true"
            style={css({
              width: "520px",
              height: "520px",
              right: "-120px",
              top: "120px",
              background: "#C9BEFF",
            })}
          ></div>
          <div
            className="bg-halo"
            aria-hidden="true"
            style={css({
              width: "420px",
              height: "420px",
              left: "-160px",
              top: "620px",
              background: "#A9BBFF",
              opacity: ".45",
            })}
          ></div>
          <div className="wrap" style={css({ position: "relative" })}>
            <span className="eyebrow">{"Возможности"}</span>
            <h2 className="sec-title" id="featTitle" data-reveal="up">
              {"Три направления —"}
              <br />
              {"одна логика времени"}
            </h2>
            <p className="sec-lead" data-reveal="up">
              {
                "Отдельные расчёты для жителей города, для поездок между городами и для грузов. Разные данные, одинаково честные статусы: если сведений нет, Waydex так и говорит."
              }
            </p>
            <div className="dirs">
              <article className="dir dir-city" data-reveal="up">
                <span className="dnum">{"01 · В ГОРОДЕ"}</span>
                <h3>{"Сколько минут до поликлиники, школы и вокзала"}</h3>
                <p>
                  {
                    "Сетка точек через каждый километр. Для каждой — полное время до ближайшего подходящего объекта пешком, на общественном транспорте или на машине. Способы передвижения считаются отдельно."
                  }
                </p>
                <MiniGrid cols={14} rows={7} detailed />
                <div className="cats" aria-label="Категории объектов">
                  <span>{"Взрослые поликлиники"}</span>
                  <span>{"Детские поликлиники"}</span>
                  <span>{"Детсады"}</span>
                  <span>{"Школы"}</span>
                  <span>{"Колледжи и вузы"}</span>
                  <span>{"Ж/д вокзалы"}</span>
                  <span>{"Автовокзалы"}</span>
                  <span>{"Аэропорты"}</span>
                  <span>{"Торговые центры"}</span>
                </div>
                <div className="geo-chips">
                  <span className="gchip">
                    <Icon name="map-pin" />
                    {"Москва"}
                  </span>
                  <span className="gchip">
                    <Icon name="map-pin" />
                    {"Калининград"}
                  </span>
                  <span className="gchip">
                    <Icon name="map-pin" />
                    {"Новороссийск"}
                  </span>
                </div>
              </article>
              <article className="dir dir-pass" data-reveal="up">
                <div className="relief" aria-hidden="true"></div>
                <span className="dnum">{"02 · ПАССАЖИРСКИЕ СВЯЗИ"}</span>
                <h3>{"Как связаны два города или два региона"}</h3>
                <p>
                  {
                    "Одна направленная пара: А → Б. Время между вокзалами и аэропортами, число отправлений, интервалы, пересадки — отдельно для поезда, самолёта, автобуса, машины и парома."
                  }
                </p>
                <div className="pairviz" aria-hidden="true">
                  <svg
                    viewBox="0 0 400 200"
                    preserveAspectRatio="xMidYMid meet"
                  >
                    <path
                      d="M40 150 C 140 20, 260 20, 360 120"
                      fill="none"
                      stroke="rgba(255,255,255,.9)"
                      strokeWidth="2.5"
                      strokeLinecap="round"
                    ></path>
                    <path
                      d="M40 150 C 160 90, 260 110, 360 120"
                      fill="none"
                      stroke="rgba(201,190,255,.65)"
                      strokeWidth="1.5"
                      strokeDasharray="2 6"
                      strokeLinecap="round"
                    ></path>
                    <circle cx="40" cy="150" r="9" fill="#fff"></circle>
                    <circle cx="360" cy="120" r="9" fill="#fff"></circle>
                    <circle
                      cx="360"
                      cy="120"
                      r="20"
                      fill="#fff"
                      opacity=".18"
                    ></circle>
                    <text
                      x="40"
                      y="186"
                      fill="#fff"
                      fontSize="15"
                      fontWeight="800"
                      textAnchor="middle"
                      fontFamily="Manrope"
                    >
                      {"А"}
                    </text>
                    <text
                      x="360"
                      y="160"
                      fill="#fff"
                      fontSize="15"
                      fontWeight="800"
                      textAnchor="middle"
                      fontFamily="Manrope"
                    >
                      {"Б"}
                    </text>
                  </svg>
                </div>
                <div className="modes-row">
                  <span className="mode-pill">
                    <Icon name="train-front" />
                    {"Поезд"}
                  </span>
                  <span className="mode-pill">
                    <Icon name="plane" />
                    {"Самолёт"}
                  </span>
                  <span className="mode-pill">
                    <Icon name="bus-front" />
                    {"Автобус"}
                  </span>
                  <span className="mode-pill">
                    <Icon name="car-front" />
                    {"Авто"}
                  </span>
                  <span className="mode-pill">
                    <Icon name="ship" />
                    {"Паром"}
                  </span>
                </div>
                <div className="geo-chips">
                  <span className="gchip">{"Цель — города России"}</span>
                  <span className="gchip ghost">
                    {"Расчёт там, где есть данные"}
                  </span>
                </div>
              </article>
              <article className="dir dir-freight" data-reveal="up">
                <div>
                  <span className="dnum">{"03 · ГРУЗОВЫЕ СВЯЗИ"}</span>
                  <h3>{"Где грузу удобно стартовать и куда он доедет"}</h3>
                  <p>
                    {
                      "В трёх городах — путь от предприятий и складов до подходящих терминалов, портов и грузовых станций. Между территориями — подтверждённые грузовые узлы по профилю груза: дороги, железная дорога, воздух, вода. Трубопроводы — только между территориями: нефть, нефтепродукты и газ отдельно."
                    }
                  </p>
                  <div className="geo-chips">
                    <span className="gchip">
                      {"Внутри Москвы, Калининграда, Новороссийска"}
                    </span>
                    <span className="gchip">
                      {"Город → город · субъект → субъект"}
                    </span>
                  </div>
                </div>
                <svg
                  className="freightviz"
                  viewBox="0 0 640 320"
                  role="img"
                  aria-label="Схема: предприятие и склад связаны с терминалом, портом и грузовой станцией; между территориями — пять видов транспорта"
                >
                  <defs>
                    <linearGradient id="gF" x1="0" x2="1">
                      <stop offset="0" stopColor="#3AD7F2"></stop>
                      <stop offset="1" stopColor="#7A52FF"></stop>
                    </linearGradient>
                  </defs>
                  <g
                    fill="none"
                    stroke="url(#gF)"
                    strokeWidth="2"
                    strokeLinecap="round"
                  >
                    <path d="M92 92 C 200 92, 220 60, 330 60"></path>
                    <path d="M92 92 C 200 92, 220 160, 330 160"></path>
                    <path d="M92 228 C 200 228, 220 160, 330 160"></path>
                    <path d="M92 228 C 200 228, 220 260, 330 260"></path>
                    <path
                      d="M380 160 C 460 160, 470 160, 560 160"
                      strokeDasharray="3 8"
                      stroke="#C9BEFF"
                    ></path>
                  </g>
                  <g
                    fontFamily="Manrope"
                    fontWeight="700"
                    fontSize="13"
                    fill="#fff"
                  >
                    <circle
                      cx="70"
                      cy="92"
                      r="22"
                      fill="#1B1F5E"
                      stroke="#3AD7F2"
                      strokeWidth="2"
                    ></circle>
                    <text x="70" y="136" textAnchor="middle">
                      {"Предприятие"}
                    </text>
                    <rect
                      x="48"
                      y="206"
                      width="44"
                      height="44"
                      rx="12"
                      fill="#1B1F5E"
                      stroke="#3AD7F2"
                      strokeWidth="2"
                    ></rect>
                    <text x="70" y="274" textAnchor="middle">
                      {"Склад"}
                    </text>
                    <rect
                      x="330"
                      y="38"
                      width="190"
                      height="44"
                      rx="22"
                      fill="#1B1F5E"
                      stroke="#7A52FF"
                      strokeWidth="2"
                    ></rect>
                    <text x="425" y="65" textAnchor="middle">
                      {"Контейнерный терминал"}
                    </text>
                    <rect
                      x="330"
                      y="138"
                      width="150"
                      height="44"
                      rx="22"
                      fill="#2F5BFF"
                    ></rect>
                    <text x="405" y="165" textAnchor="middle">
                      {"Порт"}
                    </text>
                    <rect
                      x="330"
                      y="238"
                      width="180"
                      height="44"
                      rx="22"
                      fill="#1B1F5E"
                      stroke="#7A52FF"
                      strokeWidth="2"
                    ></rect>
                    <text x="420" y="265" textAnchor="middle">
                      {"Грузовая станция"}
                    </text>
                    <circle
                      cx="590"
                      cy="160"
                      r="26"
                      fill="none"
                      stroke="#C9BEFF"
                      strokeWidth="2"
                      strokeDasharray="3 5"
                    ></circle>
                    <text x="590" y="205" textAnchor="middle" fill="#C9BEFF">
                      {"Б"}
                    </text>
                  </g>
                </svg>
              </article>
            </div>
          </div>
        </section>
        <section
          className="sec"
          id="transport"
          aria-labelledby="trTitle"
          style={css({ background: "var(--wx-pale)", paddingTop: "0" })}
        >
          <div className="wrap">
            <div className="tr-head">
              <div>
                <span className="eyebrow">{"Виды транспорта"}</span>
                <h2 className="sec-title" id="trTitle" data-reveal="up">
                  {"Пешком, по рельсам, по воздуху и по воде"}
                </h2>
              </div>
              <div
                className="seg-switch"
                role="tablist"
                aria-label="Пассажиры или грузы"
              >
                <button
                  role="tab"
                  aria-selected={group === "pax"}
                  data-trgroup="pax"
                  onClick={() => setGroup("pax")}
                  id="trTabPax"
                  aria-controls="trList"
                >
                  {"Пассажиры"}
                </button>
                <button
                  role="tab"
                  aria-selected={group === "cargo"}
                  data-trgroup="cargo"
                  onClick={() => setGroup("cargo")}
                  id="trTabCargo"
                  aria-controls="trList"
                >
                  {"Грузы"}
                </button>
              </div>
            </div>
            <TransportShowcase group={group} />
            <div className="filmstrip" aria-hidden="true">
              <div className="film" data-reveal="clip">
                <img
                  src="/assets/passenger-train.webp"
                  width="1316"
                  height="1372"
                  alt=""
                  loading="lazy"
                  decoding="async"
                />
                <span className="cap">
                  <Icon name="train-front" />
                  {"Поезд"}
                </span>
              </div>
              <div className="film" data-reveal="clip">
                <img
                  src="/assets/passenger-plane.webp"
                  width="1110"
                  height="1354"
                  alt=""
                  loading="lazy"
                  decoding="async"
                />
                <span className="cap">
                  <Icon name="plane" />
                  {"Самолёт"}
                </span>
              </div>
              <div className="film" data-reveal="clip">
                <img
                  src="/assets/car-aerial.jpg"
                  width="986"
                  height="1376"
                  alt=""
                  loading="lazy"
                  decoding="async"
                />
                <span className="cap">
                  <Icon name="car-front" />
                  {"Личный автомобиль"}
                </span>
              </div>
            </div>
          </div>
        </section>
        <section
          className="sec"
          id="how"
          aria-labelledby="howTitle"
          style={css({ background: "var(--wx-pale)", paddingTop: "0" })}
        >
          <div className="wrap">
            <span className="eyebrow">{"Как пользоваться"}</span>
            <h2 className="sec-title" id="howTitle" data-reveal="up">
              {"От вопроса к проверенному изменению"}
            </h2>
            <div className="steps" data-reveal="steps">
              <div className="steps-line" aria-hidden="true"></div>
              <div className="step">
                <span className="sn">{"01"}</span>
                <h3>{"Выберите режим и условия"}</h3>
                <p>
                  {
                    "Город, способ передвижения, категории объектов и их приоритет, дата и время."
                  }
                </p>
                <div className="ui">
                  <span className="uchip on">{"В городе"}</span>
                  <span className="uchip">{"Взрослые поликлиники"}</span>
                  <span className="uchip">{"вт 08:30"}</span>
                </div>
              </div>
              <div className="step">
                <span className="sn">{"02"}</span>
                <h3>{"Посмотрите карту"}</h3>
                <p>
                  {
                    "Цвет ячейки — индекс, рядом всегда минуты и разложение пути."
                  }
                </p>
                <div className="ui">
                  <MiniGrid cols={8} rows={3} small />
                  <b
                    className="tnum"
                    style={css({ fontSize: "20px", marginLeft: "auto" })}
                  >
                    {"44 мин"}
                  </b>
                </div>
              </div>
              <div className="step">
                <span className="sn">{"03"}</span>
                <h3>{"Спросите агента"}</h3>
                <p>
                  {
                    "Он объясняет по рассчитанным данным, откуда задержка и что можно проверить."
                  }
                </p>
                <div className="ui">
                  <span className="ubub">
                    {"Почему здесь низкая доступность поликлиник?"}
                  </span>
                </div>
              </div>
              <div className="step">
                <span className="sn">{"04"}</span>
                <h3>{"Проверьте сценарий"}</h3>
                <p>
                  {
                    "Пересчёт запускается отдельной кнопкой. До и после — на тех же точках и жителях."
                  }
                </p>
                <div className="ui">
                  <span className="ubtn">
                    <Icon name="flask-conical" />
                    {"Проверить сценарий"}
                  </span>
                </div>
              </div>
            </div>
            <p className="step-note">
              <Icon name="info" />
              {
                "Эффект появляется только после пересчёта — заранее Waydex ничего не обещает."
              }
            </p>
          </div>
        </section>
        <section
          className="sec preview-sec"
          id="toatlas"
          aria-labelledby="pvTitle"
        >
          <svg
            className="trace"
            style={css({ left: "0", top: "0", width: "100%", height: "100%" })}
            viewBox="0 0 1440 900"
            preserveAspectRatio="none"
            aria-hidden="true"
            data-reveal="draw"
          >
            <path
              className="t-main"
              style={css({ "--len": "1800" })}
              d="M0 120 C 260 160, 380 360, 720 420 S 1180 380, 1440 520"
            ></path>
            <circle className="node" cx="720" cy="420" r="5"></circle>
          </svg>
          <div className="wrap" style={css({ position: "relative" })}>
            <div className="preview-head">
              <div>
                <span className="eyebrow on-dark">{"Атлас"}</span>
                <h2
                  className="sec-title"
                  id="pvTitle"
                  data-reveal="up"
                  style={css({ color: "#fff" })}
                >
                  {"Вся картина — на одной карте"}
                </h2>
              </div>
              <div>
                <p className="sec-lead" data-reveal="up">
                  {
                    "Карта России для связей между территориями и детальные карты трёх городов. Фильтры слева, агент справа, время — на шкале суток."
                  }
                </p>
                <NavigationLink
                  className="wbtn wbtn-primary"
                  to="atlas"
                  style={css({ marginTop: "26px" })}
                >
                  {"Перейти к карте "}
                  <span className="arrow">
                    <Icon name="arrow-up-right" />
                  </span>
                </NavigationLink>
              </div>
            </div>
            <div className="preview-stage" id="previewStage">
              <div className="preview-glow" aria-hidden="true"></div>
              <NavigationLink
                className="preview-frame"
                to="atlas"
                aria-label="Открыть атлас: превью рабочего экрана"
                style={css({ display: "block", textDecoration: "none" })}
              >
                <div className="pv" aria-hidden="true">
                  <div className="pv-top">
                    <span className="wordmark">
                      <span className="wm-way">{"Way"}</span>
                      <span className="wm-dex">{"dex"}</span>
                    </span>
                    <div className="pv-tabs">
                      <span>{"В городе"}</span>
                      <span className="on">{"Пассажирские связи"}</span>
                      <span>{"Грузовые связи"}</span>
                    </div>
                    <span
                      style={css({
                        marginLeft: "auto",
                        fontWeight: "700",
                        color: "var(--wx-ink-3)",
                      })}
                    >
                      {"Москва → Калининград"}
                    </span>
                  </div>
                  <div className="pv-left">
                    <span className="h">{"Виды транспорта"}</span>
                    <div className="pv-row on">
                      {"Самолёт "}
                      <b>{"есть"}</b>
                    </div>
                    <div className="pv-row">
                      {"Поезд "}
                      <b>{"?"}</b>
                    </div>
                    <div className="pv-row">
                      {"Автомобиль "}
                      <b>{"?"}</b>
                    </div>
                    <div className="pv-row">
                      {"Автобус "}
                      <b>{"—"}</b>
                    </div>
                    <span className="h" style={css({ marginTop: "6px" })}>
                      {"Направление"}
                    </span>
                    <div className="pv-row">{"А → Б, местное время"}</div>
                  </div>
                  <div className="pv-map">
                    <PreviewMap />
                    <div className="pv-dock">
                      {"вт 10.11 · 10:15 MSK"}
                      <i></i>
                      {"24"}
                    </div>
                  </div>
                  <div className="pv-chat">
                    <span className="h" style={css({ fontWeight: "800" })}>
                      {"Агент"}
                    </span>
                    <div className="pv-bub u">
                      {"Как отличаются частота и время по видам транспорта?"}
                    </div>
                    <div className="pv-bub">
                      {
                        "Подтверждена только авиасвязь. Для поезда и машины нужны условия транзита."
                      }
                    </div>
                  </div>
                </div>
              </NavigationLink>
            </div>
          </div>
        </section>
        <section className="sec method" id="method" aria-labelledby="mTitle">
          <div className="wrap">
            <span className="eyebrow">{"Методика и данные"}</span>
            <h2 className="sec-title" id="mTitle" data-reveal="up">
              {"Честно о том, что посчитано"}
            </h2>
            <div className="method-grid">
              <div className="mcard" data-reveal="up">
                <div className="mi">
                  <Icon name="clock-3" />
                </div>
                <h3>{"Время, а не километры"}</h3>
                <p>
                  {
                    "Полный путь: пешие участки, первое ожидание, движение, переходы и ожидание пересадок. Каждый интервал учитывается один раз."
                  }
                </p>
              </div>
              <div className="mcard" data-reveal="up">
                <div className="mi">
                  <Icon name="circle-help" />
                </div>
                <h3>{"Неизвестно ≠ плохо"}</h3>
                <p>
                  {
                    "Нет данных — нейтральная штриховка и подпись. Сбой источника не превращается в низкий индекс, а подтверждённое отсутствие маршрута — отдельный статус."
                  }
                </p>
              </div>
              <div className="mcard" data-reveal="up">
                <div className="mi">
                  <Icon name="flask-conical" />
                </div>
                <h3>{"Сценарий — после проверки"}</h3>
                <p>
                  {
                    "Агент предлагает только допустимые изменения. Эффект считает расчётный модуль после кнопки «Проверить сценарий», база не перезаписывается."
                  }
                </p>
              </div>
            </div>
            <div className="method-note" data-reveal="up">
              <Icon name="triangle-alert" />
              <div>
                <b style={css({ color: "var(--wx-ink)" })}>
                  {
                    "Методика утверждена. Доступность данных проверяется отдельно."
                  }
                </b>
                {
                  dataMode === "api"
                    ? " Числа в примерах на этой странице демонстрационные. В атласе показаны результаты API с их источниками и ограничениями; наличие города в справочнике не означает полного расчёта."
                    : " В этом прототипе все числа демонстрационные. Источники — открытые данные и расписания, перечисленные в постановке; наличие города в справочнике не означает полного расчёта."
                }
              </div>
            </div>
            <div className="final" data-reveal="up">
              <svg className="trace" viewBox="0 0 600 600" aria-hidden="true">
                <path
                  d="M40 560 C 200 420, 220 260, 380 200 S 560 80, 600 40"
                  fill="none"
                  stroke="rgba(255,255,255,.55)"
                  strokeWidth="2"
                ></path>
                <path
                  d="M0 420 C 180 380, 300 420, 420 330 S 560 220, 600 230"
                  fill="none"
                  stroke="rgba(255,255,255,.3)"
                  strokeWidth="1.5"
                  strokeDasharray="2 8"
                ></path>
                <circle cx="380" cy="200" r="7" fill="#fff"></circle>
                <circle cx="420" cy="330" r="5" fill="#C9BEFF"></circle>
              </svg>
              <h2>{"Откройте атлас"}</h2>
              <p>
                {
                  "Начните с карты России или сразу выберите город: Москву, Калининград или Новороссийск."
                }
              </p>
              <NavigationLink className="wbtn wbtn-light" to="atlas">
                {"Перейти к карте "}
                <span className="arrow">
                  <Icon name="arrow-up-right" />
                </span>
              </NavigationLink>
            </div>
          </div>
        </section>
      </main>
      <footer className="lp-footer">
        <div className="wrap">
          <NavigationLink
            className="wordmark sm"
            to="top"
            aria-label="Waydex — в начало"
          >
            <span className="wm-way">{"Way"}</span>
            <span className="wm-dex">{"dex"}</span>
          </NavigationLink>
          <nav aria-label="Нижняя навигация">
            <NavigationLink to="about">{"О продукте"}</NavigationLink>
            <NavigationLink to="features">{"Возможности"}</NavigationLink>
            <NavigationLink to="how">{"Как это работает"}</NavigationLink>
            <NavigationLink to="atlas">{"Атлас"}</NavigationLink>
          </nav>
          <span>
            {dataMode === "api"
              ? "Waydex · кейс A2 · 2026. Атлас — результаты API; примеры на странице — демонстрационные."
              : "Дизайн-прототип · кейс A2 · 2026. Данные — демонстрационные."}
          </span>
        </div>
      </footer>
    </div>
  );
}
