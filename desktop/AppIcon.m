#import <Cocoa/Cocoa.h>

static NSData *DrawIcon(NSInteger size) {
    NSBitmapImageRep *bitmap = [[NSBitmapImageRep alloc]
        initWithBitmapDataPlanes:NULL
        pixelsWide:size
        pixelsHigh:size
        bitsPerSample:8
        samplesPerPixel:4
        hasAlpha:YES
        isPlanar:NO
        colorSpaceName:NSDeviceRGBColorSpace
        bytesPerRow:0
        bitsPerPixel:0];
    bitmap.size = NSMakeSize(size, size);

    NSGraphicsContext *context = [NSGraphicsContext graphicsContextWithBitmapImageRep:bitmap];
    [NSGraphicsContext saveGraphicsState];
    [NSGraphicsContext setCurrentContext:context];

    CGFloat canvas = (CGFloat)size;
    CGFloat outerInset = canvas * 0.055;
    NSRect outerRect = NSMakeRect(
        outerInset,
        outerInset,
        canvas - outerInset * 2,
        canvas - outerInset * 2
    );
    NSBezierPath *outerPath = [NSBezierPath
        bezierPathWithRoundedRect:outerRect
        xRadius:canvas * 0.22
        yRadius:canvas * 0.22];
    CGContextSetShadowWithColor(
        [NSGraphicsContext currentContext].CGContext,
        CGSizeMake(0, -canvas * 0.016),
        canvas * 0.04,
        [NSColor colorWithWhite:0 alpha:0.16].CGColor
    );
    [[NSColor colorWithCalibratedRed:0.965 green:0.965 blue:0.947 alpha:1] setFill];
    [outerPath fill];

    CGContextSetShadowWithColor(
        [NSGraphicsContext currentContext].CGContext,
        CGSizeZero,
        0,
        NULL
    );

    [[NSColor colorWithWhite:0 alpha:0.08] setStroke];
    outerPath.lineWidth = MAX(0.5, canvas * 0.003);
    [outerPath stroke];

    NSRect coreRect = NSMakeRect(canvas * 0.185, canvas * 0.185, canvas * 0.63, canvas * 0.63);
    NSBezierPath *corePath = [NSBezierPath
        bezierPathWithRoundedRect:coreRect
        xRadius:canvas * 0.16
        yRadius:canvas * 0.16];
    [[NSColor colorWithCalibratedRed:0.09 green:0.094 blue:0.106 alpha:1] setFill];
    [corePath fill];

    NSColor *glyphColor = [NSColor colorWithCalibratedRed:0.975 green:0.975 blue:0.95 alpha:1];
    CGFloat lineWidth = MAX(1.0, canvas * 0.038);
    CGFloat stemX = canvas * 0.355;
    CGFloat bottomY = canvas * 0.355;
    CGFloat middleY = canvas * 0.50;
    CGFloat topY = canvas * 0.645;

    NSBezierPath *network = [NSBezierPath bezierPath];
    network.lineWidth = lineWidth;
    network.lineCapStyle = NSLineCapStyleRound;
    [network moveToPoint:NSMakePoint(stemX, bottomY)];
    [network lineToPoint:NSMakePoint(stemX, topY)];
    [network moveToPoint:NSMakePoint(stemX, topY)];
    [network lineToPoint:NSMakePoint(canvas * 0.645, topY)];
    [network moveToPoint:NSMakePoint(stemX, middleY)];
    [network lineToPoint:NSMakePoint(canvas * 0.565, middleY)];
    [network moveToPoint:NSMakePoint(stemX, bottomY)];
    [network lineToPoint:NSMakePoint(canvas * 0.645, bottomY)];
    [glyphColor setStroke];
    [network stroke];

    CGFloat nodeRadius = MAX(1.15, canvas * 0.027);
    NSArray<NSValue *> *nodes = @[
        [NSValue valueWithPoint:NSMakePoint(canvas * 0.645, topY)],
        [NSValue valueWithPoint:NSMakePoint(canvas * 0.565, middleY)],
        [NSValue valueWithPoint:NSMakePoint(canvas * 0.645, bottomY)],
    ];
    for (NSInteger index = 0; index < nodes.count; index++) {
        NSPoint point = nodes[index].pointValue;
        NSBezierPath *node = [NSBezierPath bezierPathWithOvalInRect:NSMakeRect(
            point.x - nodeRadius,
            point.y - nodeRadius,
            nodeRadius * 2,
            nodeRadius * 2
        )];
        if (index == 0) {
            [[NSColor colorWithCalibratedRed:0.18 green:0.714 blue:0.49 alpha:1] setFill];
        } else {
            [glyphColor setFill];
        }
        [node fill];
    }

    [context flushGraphics];
    [NSGraphicsContext restoreGraphicsState];
    return [bitmap representationUsingType:NSBitmapImageFileTypePNG properties:@{}];
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc != 2) {
            fprintf(stderr, "Usage: AppIcon <output.icns>\n");
            return 2;
        }

        NSString *outputPath = [NSString stringWithUTF8String:argv[1]];
        NSArray<NSArray *> *icons = @[
            @[@"icp4", @16],
            @[@"icp5", @32],
            @[@"icp6", @64],
            @[@"ic07", @128],
            @[@"ic08", @256],
            @[@"ic09", @512],
            @[@"ic10", @1024],
        ];

        NSMutableData *container = [NSMutableData data];
        [container appendBytes:"icns" length:4];
        uint32_t placeholder = 0;
        [container appendBytes:&placeholder length:sizeof(placeholder)];

        for (NSArray *icon in icons) {
            const char *type = [icon[0] UTF8String];
            NSData *png = DrawIcon([icon[1] integerValue]);
            uint32_t chunkLength = CFSwapInt32HostToBig((uint32_t)png.length + 8);
            [container appendBytes:type length:4];
            [container appendBytes:&chunkLength length:sizeof(chunkLength)];
            [container appendData:png];
        }

        uint32_t totalLength = CFSwapInt32HostToBig((uint32_t)container.length);
        [container replaceBytesInRange:NSMakeRange(4, 4) withBytes:&totalLength];
        if (![container writeToFile:outputPath atomically:YES]) {
            fprintf(stderr, "Unable to write %s\n", outputPath.UTF8String);
            return 1;
        }
    }
    return 0;
}
