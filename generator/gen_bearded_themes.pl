#!/usr/bin/perl
# generator/gen_bearded_themes.pl
#
# Generate web-dashboard/static/js/bearded-themes.js from the Bearded Theme
# Zed theme definition (bearded-theme/dist/zed/themes/bearded-theme.json).
#
# This produces a self-contained JavaScript data file so the web-dashboard does
# NOT depend on the (temporarily) cloned bearded-theme directory.  The generated
# file carries its own source / license attribution metadata.
#
# 依存: JSON::PP (Perl コアモジュール。外部インストール不要)
#
# 使い方:
#   perl generator/gen_bearded_themes.pl
#   # → web-dashboard/static/js/bearded-themes.js を上書き生成

use strict;
use warnings;
use FindBin qw($RealBin);
use File::Basename qw(dirname);
use File::Spec;
use JSON::PP;

# ---------------------------------------------------------------------------
# hex_to_rgb_triplet
#   #RRGGBB または #RRGGBBAA を受け取り、"R,G,B" 文字列を返す。
#   アルファ値は無視する。解析失敗時は fallback 値を返す。
# ---------------------------------------------------------------------------
sub hex_to_rgb_triplet {
    my ($color) = @_;
    $color //= '';
    $color =~ s/^\s+|\s+$//g;

    if ($color =~ /^#([0-9A-Fa-f]+)$/) {
        my $h = $1;
        $h = substr($h, 0, 6) if length($h) == 8;           # AA を除去
        $h = join('', map { $_ x 2 } split(//, $h)) if length($h) == 3;  # #RGB -> #RRGGBB
        if (length($h) == 6) {
            my $r = hex(substr($h, 0, 2));
            my $g = hex(substr($h, 2, 2));
            my $b = hex(substr($h, 4, 2));
            return "$r,$g,$b";
        }
    }
    return "99,102,241";   # fallback
}

# ---------------------------------------------------------------------------
# slugify: テーマ名 -> URL/JS キー用スラッグ
# ---------------------------------------------------------------------------
sub slugify {
    my ($name) = @_;
    my $slug = lc($name);
    $slug =~ s/[^a-z0-9]+/-/g;
    $slug =~ s/^-+|-+$//g;
    return $slug;
}

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
my $script_dir = $RealBin;
my $root_dir   = File::Spec->rel2abs(File::Spec->catdir($script_dir, '..'));

my $zed_json = File::Spec->catfile(
    $root_dir, 'bearded-theme', 'dist', 'zed', 'themes', 'bearded-theme.json',
);

unless (-f $zed_json) {
    die "ERROR: $zed_json not found\n";
}

# JSON 読み込み
open my $fh, '<:encoding(UTF-8)', $zed_json
    or die "Cannot open $zed_json: $!";
my $raw = do { local $/; <$fh> };
close $fh;

my $data = JSON::PP->new->utf8->decode($raw);

# ---------------------------------------------------------------------------
# 各テーマのカラー情報を抽出
# ---------------------------------------------------------------------------
my @themes_out;
for my $t (@{ $data->{themes} }) {
    my $s          = $t->{style};
    my $appearance = $t->{appearance};
    my $name       = $t->{name};

    my $info    = $s->{info}    // $s->{'text.accent'} // '#06b6d4';
    my $success = $s->{success} // $s->{created}       // '#10b981';

    push @themes_out, {
        name       => $name,
        appearance => $appearance,
        colors     => {
            bg          => ($s->{background}                      // '#0b0f19'),
            bgPanel     => ($s->{'panel.background'}              // $s->{background}),
            bgCard      => ($s->{'elevated_surface.background'}   // $s->{background}),
            bgHover     => ($s->{'element.hover'}                 // $s->{'element.background'} // $s->{background}),
            border      => ($s->{border}                          // '#11161f'),
            borderFocus => ($s->{'border.focused'}                // $s->{'text.accent'}),
            text        => ($s->{text}                            // $s->{foreground}),
            textMuted   => ($s->{'text.muted'}                    // $s->{text}),
            textAccent  => ($s->{'text.accent'}                   // $s->{text}),
            accent      => ($s->{'border.focused'}                // $s->{'text.accent'}),
            success     => $success,
            danger      => ($s->{error}                           // $s->{deleted}  // '#f43f5e'),
            warning     => ($s->{warning}                         // $s->{conflict} // '#f59e0b'),
            info        => $info,
        },
    };
}

# ---------------------------------------------------------------------------
# デフォルトテーマ (ダッシュボード組み込みパレット)
# ---------------------------------------------------------------------------
my $default_theme = {
    name       => 'デフォルト (ISP Dark)',
    appearance => 'dark',
    colors     => {
        bg          => '#0b0f19',
        bgPanel     => '#0f172a',
        bgCard      => 'rgba(17, 24, 39, 0.85)',
        bgHover     => 'rgba(30, 41, 59, 0.9)',
        border      => 'rgba(255, 255, 255, 0.08)',
        borderFocus => 'rgba(99, 102, 241, 0.5)',
        text        => '#f3f4f6',
        textMuted   => '#9ca3af',
        textAccent  => '#f3f4f6',
        accent      => '#6366f1',
        success     => '#10b981',
        danger      => '#f43f5e',
        warning     => '#f59e0b',
        info        => '#06b6d4',
    },
};

my $registry = {
    sourceName  => 'Bearded Theme',
    sourceAuthor => 'BeardedBear',
    sourceUrl   => 'https://github.com/BeardedBear/bearded-theme',
    licenseName => 'GPL-3.0',
    licenseUrl  => 'https://github.com/BeardedBear/bearded-theme/blob/master/LICENSE',
    defaultKey  => 'default',
    themes      => [ $default_theme, @themes_out ],
};

# ---------------------------------------------------------------------------
# 各テーマにスラッグキーを付与（重複時は -2, -3 ... サフィックスを付加）
# ---------------------------------------------------------------------------
my %used_keys;
for my $i (0 .. $#{ $registry->{themes} }) {
    my $th = $registry->{themes}[$i];
    my $key;
    if ($i == 0) {
        $key = 'default';
    } else {
        my $base = slugify($th->{name});
        $key = $base;
        my $n = 2;
        while ($used_keys{$key}) {
            $key = "${base}-${n}";
            $n++;
        }
    }
    $used_keys{$key} = 1;
    $th->{key} = $key;
}

# ---------------------------------------------------------------------------
# グロー色の注入 (accent/info から RGB 3要素を派生)
# ---------------------------------------------------------------------------
for my $th (@{ $registry->{themes} }) {
    $th->{colors}{glow1} = hex_to_rgb_triplet($th->{colors}{info});
    $th->{colors}{glow2} = hex_to_rgb_triplet($th->{colors}{success});
}

# ---------------------------------------------------------------------------
# JavaScript ファイルの生成
# ---------------------------------------------------------------------------
my $json_str = JSON::PP->new
    ->utf8(0)          # Perl 内部文字列のまま出力（open で UTF-8 指定するため）
    ->pretty(1)
    ->canonical(0)     # 挿入順を保持（Python の json.dumps と同様の挙動）
    ->encode($registry);

my $source_url   = $registry->{sourceUrl};
my $source_author = $registry->{sourceAuthor};
my $license_name = $registry->{licenseName};

my $js = <<"JS";
/* ============================================================
 * Bearded Theme color palettes for the ISP Emulation Dashboard
 * ============================================================
 * Self-contained data (does NOT depend on a cloned bearded-theme
 * repository).
 *
 * Source : $source_url
 * Author : $source_author
 * License: $license_name
 *
 * Palette data derived from the official Bearded Theme Zed theme
 * definition. Licensed under the GNU General Public License v3.0.
 * ============================================================*/
window.BEARDED_THEMES = $json_str;
JS

my $out_path = File::Spec->catfile(
    $root_dir, 'web-dashboard', 'static', 'js', 'bearded-themes.js',
);

open my $out, '>:encoding(UTF-8)', $out_path
    or die "Cannot write $out_path: $!";
print $out $js;
close $out;

my $total  = scalar @{ $registry->{themes} };
my $bearded = scalar @themes_out;
print "Wrote $out_path with $total themes ($bearded from Bearded Theme + 1 default).\n";
